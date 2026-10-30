import io

import pytest
from PIL import Image
from sqlalchemy import func, select

from garblo.models import AiJob, Garment, GarmentCandidate
from tests.helpers import auth_headers, catalogue


def test_draft_edit_reload_commit_is_durable_and_idempotent(
    client, headers, asset_id, worker, providers, db_factory
):
    job_id = catalogue(client, headers, asset_id)
    assert worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", job
    candidate_id = job["output_ref"]["candidate_ids"][0]
    assert client.get("/v1/garments", headers=headers).json() == []
    response = client.patch(
        f"/v1/candidates/{candidate_id}", headers=headers, json={"color": "red", "tags": ["office"]}
    )
    assert response.status_code == 200, response.text
    with db_factory() as fresh:
        candidate = fresh.get(GarmentCandidate, candidate_id)
        assert candidate.attributes_json["color"] == "red"
        assert candidate.original_attributes["color"] == "blue"
    reloaded = client.get(f"/v1/assets/{asset_id}/candidates", headers=headers).json()
    assert reloaded[0]["attributes"]["color"] == "red"
    payload = {"candidate_ids": [candidate_id, candidate_id]}
    first = client.post(f"/v1/assets/{asset_id}/commit", headers=headers, json=payload)
    second = client.post(f"/v1/assets/{asset_id}/commit", headers=headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(providers.calls) == 1
    garments = client.get("/v1/garments", headers=headers).json()
    assert len(garments) == 1
    assert garments[0]["color"] == "red"
    assert (
        client.patch(
            f"/v1/candidates/{candidate_id}", headers=headers, json={"color": "green"}
        ).status_code
        == 409
    )
    assert client.post(f"/v1/assets/{asset_id}/catalogize", headers=headers).status_code == 409


def test_no_clothing_does_not_create_items(client, headers, asset_id, worker, providers, db):
    providers.items = []
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["output_ref"]["result"] == "no_items"
    assert job["output_ref"]["candidate_ids"] == []
    assert db.scalar(select(func.count()).select_from(Garment)) == 0
    assert db.scalar(select(func.count()).select_from(GarmentCandidate)) == 0


def test_candidate_from_other_asset_cannot_be_committed(
    client, headers, asset_id, worker, image_bytes
):
    from tests.helpers import upload

    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    candidate_id = client.get(f"/v1/jobs/{job_id}", headers=headers).json()["output_ref"][
        "candidate_ids"
    ][0]
    second_asset = upload(client, headers, image_bytes)
    assert (
        client.post(
            f"/v1/assets/{second_asset}/commit",
            headers=headers,
            json={"candidate_ids": [candidate_id]},
        ).status_code
        == 404
    )
    assert client.get("/v1/garments", headers=headers).json() == []


def test_upload_limits_and_oversized_pixel_image(client, headers, settings, image_bytes):
    settings.max_upload_bytes = len(image_bytes) - 1
    response = client.post(
        "/v1/assets", headers=headers, files={"file": ("x.png", image_bytes, "image/png")}
    )
    assert response.status_code == 413
    settings.max_upload_bytes = 1024 * 1024
    settings.max_image_pixels = 10
    response = client.post(
        "/v1/assets", headers=headers, files={"file": ("x.png", image_bytes, "image/png")}
    )
    assert response.status_code == 415


def test_cross_owner_cannot_read_edit_commit_or_get_url(client, headers, asset_id, worker, storage):
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    candidate_id = client.get(f"/v1/jobs/{job_id}", headers=headers).json()["output_ref"][
        "candidate_ids"
    ][0]
    other = auth_headers(client, "other@example.com")
    for path in (
        f"/v1/assets/{asset_id}/candidates",
        f"/v1/assets/{asset_id}/download",
        f"/v1/jobs/{job_id}",
    ):
        assert client.get(path, headers=other).status_code == 404
    assert (
        client.patch(
            f"/v1/candidates/{candidate_id}", headers=other, json={"color": "red"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/assets/{asset_id}/commit", headers=other, json={"candidate_ids": [candidate_id]}
        ).status_code
        == 404
    )
    assert storage.downloads == []


def test_consent_revoked_before_worker_never_calls_provider(
    client, headers, asset_id, worker, providers, db
):
    from garblo.models import ConsentRecord

    job_id = catalogue(client, headers, asset_id)
    consent = db.scalar(select(ConsentRecord))
    assert client.delete(f"/v1/consents/{consent.id}", headers=headers).status_code == 204
    assert worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "failed"
    assert job["error_code"] == "consent_revoked"
    assert providers.calls == []


def test_upload_rejects_false_image_and_uses_real_mime(client, headers, image_bytes):
    response = client.post(
        "/v1/assets", headers=headers, files={"file": ("fake.jpg", b"not-image", "image/jpeg")}
    )
    assert response.status_code == 415
    response = client.post(
        "/v1/assets", headers=headers, files={"file": ("fake.jpg", image_bytes, "image/jpeg")}
    )
    assert response.status_code == 201
    assert response.json()["mime_type"] == "image/png"


def test_upload_strips_exif(client, headers, storage):
    image = Image.new("RGB", (20, 30), "white")
    exif = image.getexif()
    exif[270] = "private-location"
    body = io.BytesIO()
    image.save(body, format="JPEG", exif=exif)
    response = client.post(
        "/v1/assets", headers=headers, files={"file": ("source.jpg", body.getvalue(), "image/jpeg")}
    )
    assert response.status_code == 201
    with Image.open(io.BytesIO(next(iter(storage.objects.values())))) as normalized:
        assert not normalized.getexif()


@pytest.mark.parametrize(
    "payload", [{"tags": None}, {"tags": ["x" * 81]}, {"color": "x" * 81}, {"category": 12}]
)
def test_candidate_validation(client, headers, asset_id, worker, payload):
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    candidate_id = client.get(f"/v1/jobs/{job_id}", headers=headers).json()["output_ref"][
        "candidate_ids"
    ][0]
    assert (
        client.patch(f"/v1/candidates/{candidate_id}", headers=headers, json=payload).status_code
        == 422
    )


def test_db_write_failure_rolls_back_draft_and_success(
    client, headers, asset_id, worker, db, monkeypatch
):
    job_id = catalogue(client, headers, asset_id)
    original = worker._save_analysis

    def fail_after_write(*args):
        original(*args)
        raise RuntimeError("sensitive database details")

    monkeypatch.setattr(worker, "_save_analysis", fail_after_write)
    worker.process_next()
    db.expire_all()
    job = db.get(AiJob, job_id)
    assert job.status == "failed"
    assert job.error_code == "internal_error"
    assert "sensitive" not in job.error_message
    assert db.scalar(select(func.count()).select_from(GarmentCandidate)) == 0
