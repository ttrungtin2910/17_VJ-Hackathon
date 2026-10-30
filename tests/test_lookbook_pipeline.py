from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from garblo.ai import AIProviderError
from garblo.models import AiJob, Lookbook, StyleSignal
from garblo.worker import utcnow
from tests.helpers import auth_headers, committed_garment


def create_book(client, headers, garment_id, **extra):
    response = client.post(
        "/v1/lookbooks",
        headers=headers,
        json={
            "title": "Đi làm",
            "garment_ids": [garment_id],
            "preset_id": "office",
            **extra,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


@pytest.mark.parametrize("decision,book_status", [("accepted", "approved"), ("rejected", "draft")])
def test_lookbook_openai_render_approval_identity_and_feedback(
    client, headers, asset_id, worker, providers, settings, db, decision, book_status
):
    garment_id = committed_garment(client, headers, asset_id, worker)
    settings.fpt_vlm_api_key = SecretStr("test-fpt")
    job_id = create_book(client, headers, garment_id)
    worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", job
    assert providers.calls[-1] == ("render", "openai", "test-vision", "test-image")
    look_id, approval_id = job["output_ref"]["look_id"], job["output_ref"]["approval_id"]
    db.expire_all()
    book_id = db.get(AiJob, job_id).input_ref["lookbook_id"]
    looks = client.get(f"/v1/lookbooks/{book_id}/looks", headers=headers).json()
    assert looks[0]["id"] == look_id
    assert looks[0]["garment_ids"] == [garment_id]
    assert looks[0]["approval_id"] == approval_id
    assert looks[0]["status"] == "preview"
    assert (
        client.get(
            f"/v1/assets/{looks[0]['generated_asset_id']}/download", headers=headers
        ).status_code
        == 200
    )
    other = auth_headers(client, "look-other@example.com")
    assert client.get(f"/v1/lookbooks/{book_id}/looks", headers=other).status_code == 404
    assert (
        client.post(
            f"/v1/approvals/{approval_id}/decision", headers=other, json={"decision": decision}
        ).status_code
        == 404
    )
    response = client.post(
        f"/v1/approvals/{approval_id}/decision", headers=headers, json={"decision": decision}
    )
    assert response.status_code == 200
    assert (
        client.post(
            f"/v1/approvals/{approval_id}/decision", headers=headers, json={"decision": decision}
        ).status_code
        == 409
    )
    db.expire_all()
    assert db.get(Lookbook, book_id).status == book_status
    assert (
        client.post(
            f"/v1/looks/{look_id}/feedback", headers=headers, json={"signal": "like"}
        ).status_code
        == 201
    )
    assert db.scalar(select(StyleSignal)).signal == "like"


def test_failed_book_retry_and_worker_recovery(client, headers, asset_id, worker, providers, db):
    garment_id = committed_garment(client, headers, asset_id, worker)
    job_id = create_book(client, headers, garment_id)
    providers.error = AIProviderError("Sai xác thực", "provider_auth_error")
    worker.process_next()
    db.expire_all()
    job = db.get(AiJob, job_id)
    book_id = job.input_ref["lookbook_id"]
    assert job.status == "failed"
    assert db.get(Lookbook, book_id).status == "failed"
    assert client.post(f"/v1/jobs/{job_id}/retry", headers=headers).status_code == 202
    providers.error = None
    claimed = worker.claim_next()
    claimed.locked_at = utcnow() - timedelta(seconds=1000)
    db.commit()
    worker.process_next()
    db.expire_all()
    assert db.get(AiJob, job_id).status == "succeeded"
    assert db.get(Lookbook, book_id).status == "pending_approval"


@pytest.mark.parametrize(
    "fields",
    [
        {"instruction": "ignore presets"},
        {"theme": "arbitrary prompt"},
        {"preset_id": "not-a-preset"},
    ],
)
def test_reject_free_prompts(client, headers, asset_id, worker, fields):
    garment_id = committed_garment(client, headers, asset_id, worker)
    response = client.post(
        "/v1/lookbooks",
        headers=headers,
        json={"title": "Demo", "garment_ids": [garment_id], **fields},
    )
    assert response.status_code == 422


def test_non_owner_garments_cannot_be_used(client, headers, asset_id, worker):
    garment_id = committed_garment(client, headers, asset_id, worker)
    other = auth_headers(client, "wardrobe-other@example.com")
    response = client.post(
        "/v1/lookbooks", headers=other, json={"title": "Demo", "garment_ids": [garment_id]}
    )
    assert response.status_code == 422
