"""Hội thoại → job → SDK HTTP mock → fenced atomic edit, không gọi AI thật."""

import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from sqlalchemy import select

from garblo.ai_providers.openai_direct import OpenAIDirectProvider
from garblo.models import AiJob, ConsentRecord, Garment
from garblo.worker import JobWorker, utcnow
from tests.test_openai_direct import completion


@pytest.fixture()
def editing(client, headers, db, asset_id, settings):
    user_id = client.get("/v1/me", headers=headers).json()["id"]
    garments = [
        Garment(
            user_id=user_id,
            source_asset_id=asset_id,
            category=category,
            color=color,
            tags=["formal"],
        )
        for category, color in (
            ("shirt", "white"),
            ("shirt", "blue"),
            ("pants", "black"),
            ("shoes", "black"),
        )
    ]
    db.add_all(garments)
    db.commit()
    context = {"version": 1, "event": {"id": "work"}, "style": {"id": "formal", "source": "manual"}}
    response = client.post(
        "/v1/recommendations",
        headers={**headers, "Idempotency-Key": "worker-session"},
        json={"context": context},
    )
    assert response.status_code == 201, response.text
    session = response.json()
    outfit = session["outfits"][0]
    target = next(item["garment_id"] for item in outfit["items"] if item["role"] == "top")
    replacement = next(item.id for item in garments[:2] if item.id != target)
    locked = [item["garment_id"] for item in outfit["items"] if item["role"] != "top"]
    settings.stylist_intent_model = "test-intent"
    prefix = f"/v1/recommendations/{session['id']}/outfits/{outfit['id']}"
    request = {
        "message": "Đổi áo, giữ nguyên quần và giày",
        "expected_revision": 1,
        "locked_garment_ids": locked,
    }
    queued = client.post(
        prefix + "/messages", headers={**headers, "Idempotency-Key": "worker-message"}, json=request
    )
    assert queued.status_code == 202, queued.text
    return SimpleNamespace(
        job_id=queued.json()["job_id"],
        session=session,
        outfit=outfit,
        target=target,
        replacement=replacement,
        locked=locked,
        prefix=prefix,
        request=request,
    )


@pytest.mark.parametrize(
    "scenario", ["success", "consent", "revision", "forged", "persist_failure", "reclaim"]
)
def test_worker_intent_atomicity_and_fencing(
    client, headers, db, db_factory, settings, storage, worker, editing, monkeypatch, scenario
):
    calls = []
    provider_registry = None

    if scenario == "persist_failure":
        from garblo.recommendations import service

        original = service.apply_edit

        def failing(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("private database details")

        monkeypatch.setattr(service, "apply_edit", failing)

    def handler(request):
        calls.append(json.loads(request.content))
        assert calls[-1]["model"] == "test-intent"
        assert "tools" not in calls[-1]
        if len(calls) == 1:
            if scenario == "consent":
                consent_id = db.scalar(select(ConsentRecord.id))
                assert (
                    client.delete(f"/v1/consents/{consent_id}", headers=headers).status_code == 204
                )
            elif scenario == "revision":
                edited = client.post(
                    editing.prefix + "/edits",
                    headers=headers,
                    json={
                        "expected_revision": 1,
                        "operation": "replace",
                        "target_garment_id": editing.target,
                        "replacement_garment_id": editing.replacement,
                        "locked_garment_ids": editing.locked,
                    },
                )
                assert edited.status_code == 200, edited.text
            elif scenario == "reclaim":
                with db_factory() as other:
                    job = other.get(AiJob, editing.job_id)
                    job.locked_at = utcnow() - timedelta(seconds=1000)
                    other.commit()
                    assert JobWorker(other, storage, provider_registry, settings).process_next()
        content = json.dumps(
            {
                "target_garment_id": editing.target,
                "replacement_garment_id": "invented-id"
                if scenario == "forged"
                else editing.replacement,
                "locked_garment_ids": [],
                "clarification": None,
            }
        )
        response = completion(content)
        response["usage"] = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:

        def resolve(provider, model, image_model):
            assert provider == "openai" and model == "test-intent" and image_model is None
            return OpenAIDirectProvider(
                settings, model, client=OpenAI(api_key="test", http_client=http, max_retries=0)
            )

        provider_registry = SimpleNamespace(resolve=resolve)
        worker.providers = provider_registry
        settings.stylist_intent_model = "changed-after-enqueue"
        assert worker.process_next()

    job = client.get(f"/v1/jobs/{editing.job_id}", headers=headers).json()
    session = client.get(f"/v1/recommendations/{editing.session['id']}", headers=headers).json()
    outfit = next(item for item in session["outfits"] if item["id"] == editing.outfit["id"])
    if scenario in {"success", "reclaim"}:
        assert job["status"] == "succeeded", job
        assert outfit["revision"] == 2
        assert {item["garment_id"] for item in outfit["items"]} == {
            *editing.locked,
            editing.replacement,
        }
        replay = client.post(
            editing.prefix + "/messages",
            headers={**headers, "Idempotency-Key": "worker-message"},
            json=editing.request,
        )
        assert replay.json() == {"job_id": editing.job_id, "status": "succeeded"}
    else:
        assert job["status"] == "failed", job
        assert (
            job["error_code"]
            == {
                "consent": "consent_revoked",
                "revision": "revision_conflict",
                "forged": "invalid_outfit_edit",
                "persist_failure": "internal_error",
            }[scenario]
        )
        assert outfit["revision"] == (2 if scenario == "revision" else 1)
        assert "private" not in (job["error_message"] or "")
    audit = client.get(f"/v1/jobs/{editing.job_id}/calls", headers=headers).json()
    assert len(audit) == len(calls) == (2 if scenario == "reclaim" else 1)
    assert all(call["stage"] == "intent" and call["usage"]["total_tokens"] == 30 for call in audit)
