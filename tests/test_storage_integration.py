import os
import uuid

import httpx
import pytest

from garblo.storage import ObjectStorage

pytestmark = pytest.mark.integration


def test_private_storage_bytes_and_signed_download(settings, image_bytes):
    endpoint = os.environ.get("GARBLO_TEST_S3_ENDPOINT_URL")
    if not endpoint:
        pytest.skip("Chưa cấu hình GARBLO_TEST_S3_ENDPOINT_URL cho storage test riêng")
    settings.s3_endpoint_url = endpoint
    settings.s3_public_endpoint_url = endpoint
    settings.s3_access_key = os.environ["GARBLO_TEST_S3_ACCESS_KEY"]
    settings.s3_secret_key = os.environ["GARBLO_TEST_S3_SECRET_KEY"]
    settings.s3_bucket = "garblo-test-" + uuid.uuid4().hex
    storage = ObjectStorage(settings)
    storage._client.create_bucket(Bucket=settings.s3_bucket)
    key = "private/test.png"
    try:
        storage.put_bytes(key, image_bytes, "image/png")
        assert storage.get_bytes(key) == image_bytes
        # Public URL không được đọc; chỉ URL ký sau owner check ở HTTP mới được cấp.
        assert httpx.get(f"{endpoint}/{settings.s3_bucket}/{key}").status_code in {401, 403}
        response = httpx.get(storage.presign_get(key))
        assert response.status_code == 200
        assert response.content == image_bytes
    finally:
        storage.delete(key)
        storage._client.delete_bucket(Bucket=settings.s3_bucket)
