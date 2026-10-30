from tests.helpers import auth_headers, committed_garment


def test_wardrobe_list_get_update_filters_and_tenant(client, headers, asset_id, worker):
    garment_id = committed_garment(client, headers, asset_id, worker)
    response = client.patch(
        f"/v1/garments/{garment_id}",
        headers=headers,
        json={"category": "jacket", "tags": ["office", "winter"]},
    )
    assert response.status_code == 200
    assert client.get(f"/v1/garments/{garment_id}", headers=headers).json()["category"] == "jacket"
    assert (
        len(
            client.get(
                "/v1/garments?category=jacket&tags=office&tags=winter", headers=headers
            ).json()
        )
        == 1
    )
    assert client.get("/v1/garments?category=shirt", headers=headers).json() == []
    assert client.get("/v1/garments?tags=absent", headers=headers).json() == []
    assert (
        client.patch(f"/v1/garments/{garment_id}", headers=headers, json={"tags": None}).status_code
        == 422
    )
    other = auth_headers(client, "second@example.com")
    assert client.get("/v1/garments", headers=other).json() == []
    assert client.get(f"/v1/garments/{garment_id}", headers=other).status_code == 404
    assert (
        client.patch(f"/v1/garments/{garment_id}", headers=other, json={"color": "red"}).status_code
        == 404
    )
