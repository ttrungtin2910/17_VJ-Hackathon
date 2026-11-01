import os
import time
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
        origin = "http://localhost:5183"
        storage._client.put_bucket_cors(
            Bucket=storage.bucket,
            CORSConfiguration={
                "CORSRules": [{"AllowedOrigins": [origin], "AllowedMethods": ["GET", "HEAD"]}]
            },
        )
        cors = httpx.get(storage.presign_get(key), headers={"Origin": origin})
        assert cors.headers.get("access-control-allow-origin") == origin
        signed_short = storage._presign_client.generate_presigned_url(
            "get_object", Params={"Bucket": storage.bucket, "Key": key}, ExpiresIn=1
        )
        time.sleep(2)  # Cố ý chờ TTL trên storage thật, không phải retry polling.
        assert httpx.get(signed_short).status_code == 403
    finally:
        storage.delete(key)
        storage._client.delete_bucket(Bucket=settings.s3_bucket)
