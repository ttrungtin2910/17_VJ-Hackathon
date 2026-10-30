"""Smoke HTTP thật với Compose test không có AI keys, không gọi provider thật."""

import os
import uuid
from urllib.parse import urlparse

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_api_worker_runtime_upload_private_download_and_missing_config(image_bytes):
    url = os.environ.get("GARBLO_TEST_API_URL")
    if not url:
        pytest.skip("Chưa đặt GARBLO_TEST_API_URL tới Compose test không có AI keys")
    if urlparse(url).hostname not in {"localhost", "127.0.0.1"}:
        pytest.fail("Runtime smoke chỉ chạy trên API local test riêng")
    with httpx.Client(base_url=url, timeout=15) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ai_configured"] is False
        assert client.get("/mock").status_code == 404
        email = "runtime-" + uuid.uuid4().hex + "@example.com"
        password = "test-password-not-for-deployment"
        response = client.post(
            "/v1/auth/register",
            json={"email": email, "display_name": "Kiểm thử", "password": password},
        )
        assert response.status_code == 201
        response = client.post("/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        assert (
            client.post(
                "/v1/consents", json={"purpose": "ai_processing", "policy_version": "test"}
            ).status_code
            == 201
        )
        response = client.post(
            "/v1/assets", files={"file": ("synthetic.png", image_bytes, "image/png")}
        )
        assert response.status_code == 201
        asset_id = response.json()["id"]
        for payload in ({}, {"vlm_provider": "fpt"}):
            response = client.post(f"/v1/assets/{asset_id}/catalogize", json=payload)
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "provider_not_configured"
        download = client.get(f"/v1/assets/{asset_id}/download")
        assert download.status_code == 200
        # Không đưa URL ký hoặc token ra output/report.
        response = httpx.get(download.json()["url"])
        assert response.status_code == 200
        assert response.content == image_bytes
