def auth_headers(client, email="demo@example.com"):
    password = "correct-horse-battery-staple"
    response = client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "display_name": "Demo",
            "password": password,
        },
    )
    assert response.status_code == 201, response.text
    response = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    response = client.post(
        "/v1/consents", json={"purpose": "ai_processing", "policy_version": "v1"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return headers


def upload(client, headers, image):
    response = client.post(
        "/v1/assets", headers=headers, files={"file": ("garment.png", image, "image/png")}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def catalogue(client, headers, asset_id, payload=None):
    response = client.post(
        f"/v1/assets/{asset_id}/catalogize",
        headers=headers,
        **({"json": payload} if payload is not None else {}),
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def committed_garment(client, headers, asset_id, worker):
    job_id = catalogue(client, headers, asset_id)
    assert worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", job
    response = client.post(
        f"/v1/assets/{asset_id}/commit",
        headers=headers,
        json={"candidate_ids": job["output_ref"]["candidate_ids"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["garment_ids"][0]
