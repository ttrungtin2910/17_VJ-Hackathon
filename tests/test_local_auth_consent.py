from datetime import timedelta

import pytest
from passlib.hash import bcrypt
from sqlalchemy import select

from garblo.consent import active_ai_consent
from garblo.models import ConsentRecord, User, now_utc
from tests.helpers import auth_headers


@pytest.mark.parametrize("password", ["a" * 73, "ế" * 25])
def test_password_utf8_over_72_bytes_is_rejected(client, password):
    response = client.post(
        "/v1/auth/register",
        json={"email": "long@example.com", "display_name": "Tên", "password": password},
    )
    assert response.status_code == 422
    assert password not in response.text


def test_password_exactly_72_utf8_bytes_is_accepted(client):
    password = "ế" * 24
    assert len(password.encode("utf-8")) == 72
    response = client.post(
        "/v1/auth/register",
        json={"email": "boundary@example.com", "display_name": "Tên", "password": password},
    )
    assert response.status_code == 201
    assert (
        client.post(
            "/v1/auth/login", json={"email": "boundary@example.com", "password": password}
        ).status_code
        == 200
    )


def test_legacy_login_different_suffix_is_not_accepted(client, db):
    prefix = "a" * 72
    db.add(
        User(
            email="legacy@example.com",
            display_name="Cũ",
            password_hash=bcrypt.hash(prefix + "old"),
        )
    )
    db.commit()
    for password in [prefix + "new", prefix + "old"]:
        response = client.post(
            "/v1/auth/login", json={"email": "legacy@example.com", "password": password}
        )
        assert response.status_code == 401
        assert password not in response.text


def test_latest_consent_is_owned_and_does_not_revive_old_grant(client, db):
    headers = auth_headers(client)
    user_id = client.get("/v1/me", headers=headers).json()["id"]
    other = auth_headers(client, "other@example.com")
    old = db.scalar(select(ConsentRecord).where(ConsentRecord.user_id == user_id))
    latest = ConsentRecord(
        user_id=user_id,
        purpose="ai_processing",
        policy_version="local-wsl-1",
        granted_at=old.granted_at + timedelta(days=1),
        revoked_at=now_utc(),
    )
    db.add(latest)
    db.commit()
    response = client.get("/v1/me/consents/latest?purpose=ai_processing", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == latest.id
    assert response.json()["revoked_at"] is not None
    assert not active_ai_consent(db, user_id)
    foreign = client.get("/v1/me/consents/latest?purpose=ai_processing", headers=other)
    assert foreign.status_code == 200
    assert foreign.json()["id"] != latest.id
    empty = client.get("/v1/me/consents/latest?purpose=unknown", headers=headers)
    assert empty.status_code == 200 and empty.json() is None
    assert client.get("/v1/me/consents/latest").status_code == 401
    assert client.delete(f"/v1/consents/{latest.id}", headers=other).status_code == 404


def test_revoke_is_repeatable(client, headers):
    grant = client.post(
        "/v1/consents",
        headers=headers,
        json={"purpose": "ai_processing", "policy_version": "local-wsl-1"},
    ).json()
    url = f"/v1/consents/{grant['id']}"
    assert client.delete(url, headers=headers).status_code == 204
    first = client.get("/v1/me/consents/latest", headers=headers).json()
    assert client.delete(url, headers=headers).status_code == 204
    assert client.get("/v1/me/consents/latest", headers=headers).json() == first
    assert first["revoked_at"] is not None
