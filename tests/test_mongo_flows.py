from typing import Any

from fastapi.testclient import TestClient
from pymongo.database import Database

from garblo.models import document


PASSWORD = "correct-horse-battery-staple"


def register_and_login(client: TestClient, email: str) -> tuple[str, dict[str, Any]]:
    assert client.post(
        "/v1/auth/register",
        json={"email": email, "display_name": "Demo", "password": PASSWORD},
    ).status_code == 201
    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    profile = client.get("/v1/me", headers={"Authorization": "Bearer " + login.json()["access_token"]})
    return login.json()["access_token"], profile.json()


def test_consent_can_be_revoked(client: TestClient, database: Database) -> None:
    token, profile = register_and_login(client, "consent@example.com")
    headers = {"Authorization": "Bearer " + token}
    consent = client.post("/v1/consents", json={"purpose": "ai_processing", "policy_version": "v1"}, headers=headers)
    assert consent.status_code == 201
    assert client.delete("/v1/consents/{}".format(consent.json()["id"]), headers=headers).status_code == 204
    stored = database.consent_records.find_one({"id": consent.json()["id"], "user_id": profile["id"]})
    assert stored and stored["revoked_at"] is not None


def test_jobs_are_scoped_to_their_owner(client: TestClient, database: Database) -> None:
    owner_token, owner = register_and_login(client, "owner@example.com")
    other_token, _ = register_and_login(client, "other@example.com")
    job = document(
        user_id=owner["id"],
        job_type="catalogue",
        input_ref={},
        output_ref={},
        status="queued",
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    database.ai_jobs.insert_one(job)
    response = client.get("/v1/jobs/{}".format(job["id"]), headers={"Authorization": "Bearer " + other_token})
    assert response.status_code == 404
    assert client.get("/v1/jobs/{}".format(job["id"]), headers={"Authorization": "Bearer " + owner_token}).status_code == 200


def test_approval_decision_updates_look_and_lookbook(client: TestClient, database: Database) -> None:
    token, user = register_and_login(client, "approval@example.com")
    lookbook = document(user_id=user["id"], title="Weekend", theme=None, occasion=None, status="pending_approval", cover_asset_id=None)
    look = document(
        lookbook_id=lookbook["id"],
        generated_asset_id=None,
        generation_prompt="test",
        model_reference=None,
        cmax_score=None,
        status="preview",
    )
    approval = document(
        user_id=user["id"],
        subject_type="look",
        subject_id=look["id"],
        action="save_look",
        status="pending",
        decision_payload={},
        requested_at=look["created_at"],
        decided_at=None,
    )
    database.lookbooks.insert_one(lookbook)
    database.looks.insert_one(look)
    database.approval_requests.insert_one(approval)

    response = client.post(
        "/v1/approvals/{}/decision".format(approval["id"]),
        json={"decision": "accepted", "note": "Looks good"},
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert database.looks.find_one({"id": look["id"]})["status"] == "accepted"
    assert database.lookbooks.find_one({"id": lookbook["id"]})["status"] == "approved"
