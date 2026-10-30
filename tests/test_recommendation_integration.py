"""Nghiệm thu B trên PostgreSQL thật, không inference/image generation thật."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from garblo.models import AiJob
from garblo.recommendations.models import RecommendationFeedback, RecommendationSession
from garblo.worker import JobWorker
from tests.helpers import auth_headers, catalogue, upload
from tests.test_postgres_integration import postgres_runtime as _postgres_runtime

postgres_runtime = _postgres_runtime

pytestmark = pytest.mark.integration
CONTEXT = {
    "version": 1,
    "event": {"id": "work"},
    "style": {"id": "formal", "source": "manual"},
    "weather": None,
    "transports": [],
}


def wardrobe(runtime, image_bytes, storage, providers, settings):
    client = runtime.client
    headers = auth_headers(client)
    providers.items = [
        {
            "category": category,
            "color": color,
            "material": None,
            "season": None,
            "tags": ["formal"],
            "details": {"styles": ["formal"]},
        }
        for category, color in (
            ("áo", "trắng"),
            ("shirt", "blue"),
            ("quần", "đen"),
            ("giày", "đen"),
            ("shoes", "brown"),
        )
    ]
    asset_id = upload(client, headers, image_bytes)
    job_id = catalogue(client, headers, asset_id)
    with runtime.factory() as db:
        worker = JobWorker(db, storage, providers, settings)
        worker.process_next()
        assert db.get(AiJob, job_id).status == "succeeded"
        candidate_ids = db.get(AiJob, job_id).output_ref["candidate_ids"]
    committed = client.post(
        f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": candidate_ids}
    )
    assert committed.status_code == 200, committed.text
    return headers, committed.json()["garment_ids"]


def test_postgres_recommendation_concurrent_create_edit_feedback_and_snapshots(
    postgres_runtime, image_bytes, storage, providers, settings
):
    runtime = postgres_runtime
    client = runtime.client
    headers, garment_ids = wardrobe(runtime, image_bytes, storage, providers, settings)
    barrier = Barrier(2)

    def create():
        barrier.wait(timeout=10)
        response = client.post(
            "/v1/recommendations",
            headers={**headers, "Idempotency-Key": "same-request"},
            json={"context": CONTEXT},
        )
        assert response.status_code in {200, 201}, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        sessions = [future.result(timeout=20) for future in futures]
    first = sessions[0]
    assert first["id"] == sessions[1]["id"]
    assert len(first["outfits"]) == 3
    assert all(
        item["garment_id"] in garment_ids for outfit in first["outfits"] for item in outfit["items"]
    )
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationSession)) == 1

    outfit = first["outfits"][0]
    target = next(item["garment_id"] for item in outfit["items"] if item["role"] == "shoes")
    replacement = next(garment_id for garment_id in garment_ids[3:] if garment_id != target)
    locked = [item["garment_id"] for item in outfit["items"] if item["role"] != "shoes"]
    barrier = Barrier(2)

    def edit():
        barrier.wait(timeout=10)
        response = client.post(
            f"/v1/recommendations/{first['id']}/outfits/{outfit['id']}/edits",
            headers=headers,
            json={
                "expected_revision": 1,
                "operation": "replace",
                "target_garment_id": target,
                "replacement_garment_id": replacement,
                "locked_garment_ids": locked,
            },
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(edit) for _ in range(2)]
        statuses = [future.result(timeout=20) for future in futures]
    assert sorted(statuses) == [200, 409]
    current = client.get(f"/v1/recommendations/{first['id']}", headers=headers).json()
    changed = next(item for item in current["outfits"] if item["id"] == outfit["id"])
    assert changed["revision"] == 2
    assert {item["garment_id"] for item in changed["items"]} == {*locked, replacement}

    barrier = Barrier(2)

    def feedback():
        barrier.wait(timeout=10)
        response = client.put(
            f"/v1/recommendations/{first['id']}/outfits/{outfit['id']}/feedback",
            headers=headers,
            json={"signal": "like"},
        )
        assert response.status_code == 200, response.text

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(feedback) for _ in range(2)]
        for future in futures:
            future.result(timeout=20)
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationFeedback)) == 1

    next_response = client.post(
        "/v1/recommendations",
        headers={**headers, "Idempotency-Key": "next-request"},
        json={"context": CONTEXT},
    )
    assert next_response.status_code == 201, next_response.text
    assert next_response.json()["weights_snapshot"]
    assert all(abs(weight) <= 3 for weight in next_response.json()["weights_snapshot"].values())
    old = client.get(f"/v1/recommendations/{first['id']}", headers=headers).json()
    assert old["weights_snapshot"] == first["weights_snapshot"]
    assert all(item["wear_count"] == 0 for look in old["outfits"] for item in look["items"])

    original = old["outfits"][0]["items"][0]
    response = client.patch(
        f"/v1/garments/{original['garment_id']}",
        headers=headers,
        json={"color": "new-color", "catalog_status": "archived"},
    )
    assert response.status_code == 200
    reloaded = client.get(f"/v1/recommendations/{first['id']}", headers=headers).json()
    stored = next(
        item
        for item in reloaded["outfits"][0]["items"]
        if item["garment_id"] == original["garment_id"]
    )
    assert stored["color"] == original["color"]


@pytest.mark.parametrize("scenario", ["success", "revision", "persist_failure"])
def test_postgres_conversation_worker_uses_atomic_edit_transaction(
    postgres_runtime, image_bytes, storage, providers, settings, monkeypatch, scenario
):
    import json
    from types import SimpleNamespace

    import httpx
    from openai import OpenAI

    from garblo.ai_providers.openai_direct import OpenAIDirectProvider
    from garblo.recommendations import service
    from tests.test_openai_direct import completion

    runtime = postgres_runtime
    client = runtime.client
    headers, garment_ids = wardrobe(runtime, image_bytes, storage, providers, settings)
    session = client.post(
        "/v1/recommendations",
        headers={**headers, "Idempotency-Key": "async-session"},
        json={"context": CONTEXT},
    ).json()
    outfit = session["outfits"][0]
    target = next(item["garment_id"] for item in outfit["items"] if item["role"] == "shoes")
    replacement = next(value for value in garment_ids[3:] if value != target)
    locked = [item["garment_id"] for item in outfit["items"] if item["role"] != "shoes"]
    prefix = f"/v1/recommendations/{session['id']}/outfits/{outfit['id']}"
    settings.stylist_intent_model = "intent-pg-test"
    queued = client.post(
        prefix + "/messages",
        headers={**headers, "Idempotency-Key": "async-message"},
        json={
            "message": "Đổi giày, giữ nguyên áo và quần",
            "expected_revision": 1,
            "locked_garment_ids": locked,
        },
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    if scenario == "persist_failure":
        original = service.apply_edit

        def fail_after_flush(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("private transaction failure")

        monkeypatch.setattr(service, "apply_edit", fail_after_flush)

    def handler(request):
        if scenario == "revision":
            edited = client.post(
                prefix + "/edits",
                headers=headers,
                json={
                    "operation": "replace",
                    "expected_revision": 1,
                    "target_garment_id": target,
                    "replacement_garment_id": replacement,
                    "locked_garment_ids": locked,
                },
            )
            assert edited.status_code == 200, edited.text
        return httpx.Response(
            200,
            json=completion(
                json.dumps(
                    {
                        "target_garment_id": target,
                        "replacement_garment_id": replacement,
                        "locked_garment_ids": [],
                        "clarification": None,
                    }
                )
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        registry = SimpleNamespace(
            resolve=lambda provider, model, image_model: OpenAIDirectProvider(
                settings, model, client=OpenAI(api_key="test", http_client=http, max_retries=0)
            )
        )
        with runtime.factory() as db:
            assert JobWorker(db, storage, registry, settings).process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    updated = client.get(f"/v1/recommendations/{session['id']}", headers=headers).json()["outfits"][
        0
    ]
    if scenario == "success":
        assert job["status"] == "succeeded", job
        assert updated["revision"] == job["output_ref"]["revision"] == 2
    else:
        assert job["status"] == "failed", job
        assert job["error_code"] == (
            "revision_conflict" if scenario == "revision" else "internal_error"
        )
        assert updated["revision"] == (2 if scenario == "revision" else 1)
        assert "private" not in job["error_message"]
