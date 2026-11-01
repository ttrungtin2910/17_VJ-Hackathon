import io
from datetime import timedelta

from PIL import Image
from sqlalchemy import func, select

from garblo.models import AiJob, MediaAsset, now_utc
from tests.helpers import auth_headers, catalogue, upload

KEY = "upload-operation-001"


def keyed_upload(client, headers, image, key=KEY, asset_type="garment_photo"):
    return client.post(
        "/v1/assets",
        headers={**headers, "Idempotency-Key": key},
        data={"asset_type": asset_type},
        files={"file": ("photo.png", image, "image/png")},
    )


def test_upload_replay_returns_same_asset(client, headers, image_bytes, db, storage):
    first = keyed_upload(client, headers, image_bytes)
    replay = keyed_upload(client, headers, image_bytes)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert db.scalar(select(func.count()).select_from(MediaAsset)) == 1
    assert len(storage.objects) == 1


def test_upload_same_key_different_body_conflicts(client, headers, image_bytes):
    assert keyed_upload(client, headers, image_bytes).status_code == 201
    buffer = io.BytesIO()
    Image.new("RGB", (24, 32), "red").save(buffer, format="PNG")
    for image, kind in [(buffer.getvalue(), "garment_photo"), (image_bytes, "background")]:
        response = keyed_upload(client, headers, image, asset_type=kind)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "idempotency_conflict"


def test_upload_lookup_recovers_lost_response(client, headers, image_bytes):
    asset = keyed_upload(client, headers, image_bytes).json()
    response = client.get(f"/v1/assets/by-request/{KEY}", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"request_key": KEY, "status": "completed", "asset_id": asset["id"]}
    assert client.get(f"/v1/assets/{asset['id']}", headers=headers).json() == asset


def test_catalogue_lookup_uses_source_not_latest_job(client, headers, image_bytes):
    first = upload(client, headers, image_bytes)
    second = upload(client, headers, image_bytes)
    first_job = catalogue(client, headers, first)
    catalogue(client, headers, second)
    result = client.get(f"/v1/assets/{first}/jobs", headers=headers)
    assert result.status_code == 200
    assert [job["id"] for job in result.json()] == [first_job]
    assert "input_ref" not in result.text
    third = upload(client, headers, image_bytes)
    assert client.get(f"/v1/assets/{third}/jobs", headers=headers).json() == []
    assert client.post(f"/v1/assets/{first}/catalogize", headers=headers).status_code == 409
    assert client.get(f"/v1/assets/{first}/jobs", headers=headers).json() == result.json()


def test_foreign_operation_is_not_visible(client, headers, image_bytes):
    first = keyed_upload(client, headers, image_bytes).json()["id"]
    other = auth_headers(client, "other@example.com")
    for path in [f"by-request/{KEY}", first, f"{first}/jobs"]:
        assert client.get(f"/v1/assets/{path}", headers=other).status_code == 404
    own = keyed_upload(client, other, image_bytes)
    assert own.status_code == 201 and own.json()["id"] != first


def test_ambiguous_catalogue_lookup_is_explicit(client, headers, asset_id, db):
    user_id = client.get("/v1/me", headers=headers).json()["id"]
    for _ in range(2):
        db.add(AiJob(user_id=user_id, job_type="catalogue", input_ref={"asset_id": asset_id}))
    db.commit()
    response = client.get(f"/v1/assets/{asset_id}/jobs", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ambiguous_catalogue_jobs"


def test_upload_failed_storage_can_resume_without_second_asset(
    client, headers, image_bytes, db, storage, monkeypatch
):
    from garblo.models import UploadOperation

    put = storage.put_bytes

    def unavailable(*_):
        assert not db.in_transaction()
        raise RuntimeError("Không ghi được")

    monkeypatch.setattr(storage, "put_bytes", unavailable)
    assert keyed_upload(client, headers, image_bytes).status_code == 503
    operation = db.scalar(select(UploadOperation))
    reserved_id = operation.reserved_asset_id
    db.rollback()
    lookup = client.get(f"/v1/assets/by-request/{KEY}", headers=headers).json()
    assert lookup["status"] == "failed" and lookup["asset_id"] is None
    monkeypatch.setattr(storage, "put_bytes", put)
    response = keyed_upload(client, headers, image_bytes)
    assert response.status_code == 201 and response.json()["id"] == reserved_id
    assert db.scalar(select(func.count()).select_from(MediaAsset)) == 1


def test_pending_upload_requires_lease_expiry_before_recovery(
    client, headers, image_bytes, db, storage
):
    from garblo.models import UploadOperation

    first = keyed_upload(client, headers, image_bytes)
    operation = db.scalar(select(UploadOperation))
    operation.status, operation.asset_id = "pending", None
    operation.lease_expires_at = now_utc() + timedelta(days=1)
    db.commit()
    assert keyed_upload(client, headers, image_bytes).status_code == 409
    operation.lease_expires_at = now_utc() - timedelta(days=1)
    db.commit()
    replay = keyed_upload(client, headers, image_bytes)
    assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]
    assert len(storage.objects) == 1
