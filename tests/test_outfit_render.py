import io

from PIL import Image
from sqlalchemy import func, select

from garblo.models import AiJob, Garment
from tests.helpers import auth_headers, upload
from tests.test_recommendations import create, outfit_path
from tests.test_recommendations import wardrobe as wardrobe  # noqa: F401


def test_render_resume_approval_and_owned_images(
    client, headers, wardrobe, worker, db, providers, storage, image_bytes
):
    items = wardrobe(categories=("shirt", "pants", "shoes"))
    crop = io.BytesIO()
    Image.new("RGB", (24, 32), "red").save(crop, format="PNG")
    crop_bytes = crop.getvalue()
    crop_id = upload(client, headers, crop_bytes)
    items[0].component_asset_id = crop_id
    db.commit()
    session = create(client, headers)
    path = outfit_path(session) + "/render"
    assert client.get(path, headers=headers).json() is None
    first = client.post(path, headers=headers, json={"expected_revision": 1})
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]
    assert (
        client.post(path, headers=headers, json={"expected_revision": 1}).json()["job_id"] == job_id
    )
    other = auth_headers(client, "render-other@example.com")
    assert client.get(path, headers=other).status_code == 404
    assert client.post(path, headers=other, json={"expected_revision": 1}).status_code == 404
    assert client.post(path, headers=headers, json={"expected_revision": 2}).status_code == 409
    assert worker.process_next()
    result = client.get(path, headers=headers).json()
    assert result["status"] == "succeeded", result
    assert result["generated_asset_id"] and result["approval_status"] == "pending"
    # Fake provider ghi đúng bytes mà worker truyền, không chỉ metadata.
    assert providers.render_images
    images = {name: body for name, body, _ in providers.render_images}
    assert images[f"{items[0].id}.png"] == crop_bytes
    assert images[f"{items[1].id}.png"] == image_bytes
    assert (
        client.post(
            f"/v1/approvals/{result['approval_id']}/decision",
            headers=headers,
            json={"decision": "accepted"},
        ).status_code
        == 200
    )
    assert client.get(path, headers=headers).json()["approval_status"] == "accepted"
    assert (
        db.scalar(select(func.count()).select_from(AiJob).where(AiJob.job_type == "lookbook")) == 1
    )


def test_render_requires_consent_and_active_garments(client, headers, wardrobe, db):
    wardrobe()
    session = create(client, headers)
    path = outfit_path(session) + "/render"
    garment_id = session["outfits"][0]["items"][0]["garment_id"]
    db.get(Garment, garment_id).catalog_status = "archived"
    db.commit()
    assert client.post(path, headers=headers, json={"expected_revision": 1}).status_code == 409
    db.get(Garment, garment_id).catalog_status = "active"
    db.commit()
    consent = client.get("/v1/me/consents/latest?purpose=ai_processing", headers=headers).json()
    client.delete(f"/v1/consents/{consent['id']}", headers=headers)
    assert client.post(path, headers=headers, json={"expected_revision": 1}).status_code == 409


def test_render_rejects_foreign_component(client, headers, wardrobe, worker, db, image_bytes):
    items = wardrobe(categories=("shirt", "pants", "shoes"))
    other = auth_headers(client, "foreign-crop@example.com")
    foreign = upload(client, other, image_bytes)
    items[0].component_asset_id = foreign
    db.commit()
    session = create(client, headers)
    path = outfit_path(session) + "/render"
    client.post(path, headers=headers, json={"expected_revision": 1})
    worker.process_next()
    result = client.get(path, headers=headers).json()
    assert result["status"] == "failed" and result["error_code"] == "source_not_found"


def test_render_configuration_and_revoke_after_enqueue(
    client, headers, wardrobe, settings, worker, providers
):
    wardrobe()
    session = create(client, headers)
    path = outfit_path(session) + "/render"
    settings.openai_image_model = None
    assert client.post(path, headers=headers, json={"expected_revision": 1}).status_code == 503
    settings.openai_image_model = "dall-e-3"
    assert client.post(path, headers=headers, json={"expected_revision": 1}).status_code == 503
    settings.openai_image_model = "gpt-image-test"
    assert client.post(path, headers=headers, json={"expected_revision": 1}).status_code == 202
    consent = client.get("/v1/me/consents/latest?purpose=ai_processing", headers=headers).json()
    client.delete(f"/v1/consents/{consent['id']}", headers=headers)
    worker.process_next()
    result = client.get(path, headers=headers).json()
    assert result["status"] == "failed" and result["error_code"] == "consent_revoked"
    assert not providers.calls
