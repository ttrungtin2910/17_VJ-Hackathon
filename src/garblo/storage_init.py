"""Tạo bucket private cho local Compose; không chạy tự động khi API khởi động."""

import time

from botocore.exceptions import ClientError

from garblo.config import get_settings
from garblo.storage import ObjectStorage


def run() -> None:
    settings = get_settings()
    storage = ObjectStorage(settings)
    for attempt in range(30):
        try:
            try:
                storage._client.head_bucket(Bucket=storage.bucket)
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
                options = (
                    {}
                    if storage.settings.s3_region == "us-east-1"
                    else {
                        "CreateBucketConfiguration": {
                            "LocationConstraint": storage.settings.s3_region
                        }
                    }
                )
                storage._client.create_bucket(Bucket=storage.bucket, **options)
            # Không gắn public policy/ACL. Bucket mới mặc định private.
            if settings.local_storage_cors_enabled:
                storage._client.put_bucket_cors(
                    Bucket=storage.bucket,
                    CORSConfiguration={
                        "CORSRules": [
                            {
                                "AllowedOrigins": settings.cors_origin_list,
                                "AllowedMethods": ["GET", "HEAD"],
                                "AllowedHeaders": ["*"],
                                "ExposeHeaders": ["ETag"],
                                "MaxAgeSeconds": 300,
                            }
                        ]
                    },
                )
            return
        except Exception:
            if attempt == 29:
                raise RuntimeError("Không khởi tạo được bucket private") from None
            time.sleep(2)


if __name__ == "__main__":
    run()
