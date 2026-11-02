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


def test_openapi_uses_http_bearer_jwt_and_missing_token_is_unauthorized(client: TestClient) -> None:
    response = client.get("/v1/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    schema = client.get("/openapi.json").json()
    scheme = schema["components"]["securitySchemes"]["BearerAuth"]
    assert scheme == {
        "type": "http",
        "description": "Paste the access_token returned by POST /v1/auth/login.",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert "OAuth2PasswordBearer" not in schema["components"]["securitySchemes"]


def test_ai_route_returns_503_before_openai_is_configured(client: TestClient) -> None:
    token = register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    consent = client.post("/v1/consents", json={"purpose": "ai_processing", "policy_version": "v1"}, headers=headers)
    assert consent.status_code == 201

    response = client.post("/v1/assets/not-an-asset/catalogize", headers=headers)
    assert response.status_code == 503
    assert "OpenAI is not configured" in response.json()["detail"]


def test_mock_ui_is_served_in_development(client: TestClient) -> None:
    response = client.get("/mock")
    assert response.status_code == 200
    assert "Garblo API Mock" in response.text

