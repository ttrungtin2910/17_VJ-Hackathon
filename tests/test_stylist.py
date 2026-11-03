"""Stylist qua HTTP + worker thật, chỉ mock HTTP provider."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from sqlalchemy import func, select

from garblo.ai_providers.openai_direct import OpenAIDirectProvider
from garblo.models import AiJob, ConsentRecord
from garblo.recommendations.models import RecommendationSession
from tests.test_openai_direct import completion
from tests.test_recommendation_worker import editing  # noqa: F401


@pytest.mark.parametrize(
    "scenario",
    [
        "create",
        "edit",
        "clarify",
        "forged",
        "locked",
        "revision",
        "consent",
        "persist",
        "schema",
    ],
)
def test_stylist_worker(client, headers, db, settings, worker, editing, monkeypatch, scenario):  # noqa: F811
    legacy = db.get(AiJob, editing.job_id)
    legacy.status = "succeeded"
    db.commit()
    create = scenario in {"create", "persist"}
    body = {"message": "Phối đồ đi làm thanh lịch" if create else "Đổi áo, giữ quần và giày"}
    if create:
        body["context"] = editing.session["context"]
    else:
        body.update(
            session_id=editing.session["id"],
            outfit_id=editing.outfit["id"],
            expected_revision=1,
            locked_garment_ids=editing.locked,
        )
    request_headers = {**headers, "Idempotency-Key": "stylist-test"}
    queued = client.post("/v1/stylist/messages", headers=request_headers, json=body)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    assert (
        client.post("/v1/stylist/messages", headers=request_headers, json=body).json()
        == queued.json()
    )
    assert (
        client.post(
            "/v1/stylist/messages", headers=request_headers, json={**body, "message": "khác"}
        ).status_code
        == 409
    )
    if scenario == "persist":
        from garblo.recommendations import service

        original = service.create_session

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("private error")

        monkeypatch.setattr(service, "create_session", fail)
    calls = []

    def handler(request):
        call = json.loads(request.content)
        calls.append(call)
        assert call["max_completion_tokens"] == 1024
        data = json.loads(call["messages"][1]["content"])
        assert len(data["candidates"]) <= 48
        assert "object_key" not in json.dumps(data)
        if scenario == "consent":
            consent_id = db.scalar(select(ConsentRecord.id))
            assert client.delete(f"/v1/consents/{consent_id}", headers=headers).status_code == 204
        if scenario == "revision":
            assert (
                client.post(
                    editing.prefix + "/edits",
                    headers=headers,
                    json={
                        "operation": "replace",
                        "expected_revision": 1,
                        "target_garment_id": editing.target,
                        "replacement_garment_id": editing.replacement,
                    },
                ).status_code
                == 200
            )
        intent = {
            "action": "recommend" if create else "replace_item",
            "context": None,
            "target_garment_id": editing.target,
            "replacement_garment_id": editing.replacement,
            "locked_garment_ids": [],
            "constraints": None,
            "clarification": None,
        }
        if scenario == "clarify":
            intent.update(action="clarify", clarification="Bạn muốn đổi món nào?")
        if scenario == "forged":
            intent["replacement_garment_id"] = "foreign-id"
        if scenario == "locked":
            intent["target_garment_id"] = editing.locked[0]
        if scenario == "schema":
            intent["action"] = "delete_wardrobe"
        response = completion(json.dumps(intent))
        response["usage"] = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        worker.providers = SimpleNamespace(
            resolve=lambda provider, model, image_model: OpenAIDirectProvider(
                settings, model, client=OpenAI(api_key="test", http_client=http, max_retries=0)
            )
        )
        assert worker.process_next()
    assert len(calls) == 1
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    session = client.get(f"/v1/recommendations/{editing.session['id']}", headers=headers).json()
    outfit = next(item for item in session["outfits"] if item["id"] == editing.outfit["id"])
    if scenario in {"create", "edit", "clarify"}:
        assert job["status"] == "succeeded", job
        assert (
            job["output_ref"]["kind"]
            == {"create": "outfits", "edit": "updated_outfit", "clarify": "clarification"}[scenario]
        )
        if create:
            assert job["output_ref"]["session"]["outfits"]
        elif scenario == "edit":
            assert outfit["revision"] == 2
            assert {i["garment_id"] for i in outfit["items"]} == {
                *editing.locked,
                editing.replacement,
            }
        else:
            assert job["output_ref"]["question"] == "Bạn muốn đổi món nào?"
            assert outfit["revision"] == 1
    else:
        assert job["status"] == "failed", job
        assert (
            job["error_code"]
            == {
                "forged": "invalid_model_output",
                "locked": "invalid_model_output",
                "schema": "invalid_model_output",
                "revision": "revision_conflict",
                "consent": "consent_revoked",
                "persist": "internal_error",
            }[scenario]
        )
        assert outfit["revision"] == (2 if scenario == "revision" else 1)
        if scenario == "persist":
            assert db.scalar(select(func.count()).select_from(RecommendationSession)) == 1
    audit = client.get(f"/v1/jobs/{job_id}/calls", headers=headers).json()
    assert len(audit) == 1 and audit[0]["prompt_version"] == "stylist-turn-v1"


@pytest.mark.parametrize(
    "body",
    [
        {"message": " "},
        {"message": "hi", "outfit_id": "x"},
        {"message": "hi", "locked_garment_ids": ["x"]},
    ],
)
def test_invalid_stylist_request(client, headers, body):
    assert (
        client.post(
            "/v1/stylist/messages", headers={**headers, "Idempotency-Key": "invalid"}, json=body
        ).status_code
        == 422
    )


def test_stylist_requires_configuration_and_consent(client, headers, db, settings):
    request = {"message": "Phối đồ đi làm"}
    h = {**headers, "Idempotency-Key": "unavailable"}
    assert client.post("/v1/stylist/messages", headers=h, json=request).status_code == 503
    settings.stylist_intent_model = "test-intent"
    consent_id = db.scalar(select(ConsentRecord.id))
    assert client.delete(f"/v1/consents/{consent_id}", headers=headers).status_code == 204
    assert client.post("/v1/stylist/messages", headers=h, json=request).status_code == 409
    assert db.scalar(select(func.count()).select_from(AiJob)) == 0


def test_foreign_outfit_cannot_be_enqueued(client, db, settings, editing):  # noqa: F811
    from tests.helpers import auth_headers

    other = auth_headers(client, "other-stylist@example.com")
    before = db.scalar(select(func.count()).select_from(AiJob))
    response = client.post(
        "/v1/stylist/messages",
        headers={**other, "Idempotency-Key": "foreign"},
        json={
            "message": "Đổi áo",
            "session_id": editing.session["id"],
            "outfit_id": editing.outfit["id"],
            "expected_revision": 1,
        },
    )
    assert response.status_code == 404
    assert db.scalar(select(func.count()).select_from(AiJob)) == before
