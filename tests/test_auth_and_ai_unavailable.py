from fastapi.testclient import TestClient

PASSWORD = "correct-horse-battery-staple"


def register_and_login(client: TestClient, email: str = "demo@example.com") -> str:
    register = client.post(
        "/v1/auth/register",
        json={"email": email, "display_name": "Demo", "password": PASSWORD},
    )
    assert register.status_code == 201, register.text
    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def test_user_can_register_login_and_read_profile(client: TestClient) -> None:
    token = register_and_login(client)
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "demo@example.com"
    assert "password_hash" not in response.text


def test_ai_route_returns_503_before_openai_is_configured(
    client: TestClient, settings, image_bytes
) -> None:
    from tests.helpers import upload

    settings.openai_api_key = None
    token = register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    consent = client.post(
        "/v1/consents", json={"purpose": "ai_processing", "policy_version": "v1"}, headers=headers
    )
    assert consent.status_code == 201

    asset_id = upload(client, headers, image_bytes)
    response = client.post(f"/v1/assets/{asset_id}/catalogize", headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_not_configured"


def test_mock_ui_is_served_in_development(client: TestClient) -> None:
    response = client.get("/mock")
    assert response.status_code == 200
    assert "Garblo API Mock" in response.text
