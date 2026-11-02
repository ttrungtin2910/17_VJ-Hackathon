from __future__ import annotations

from dataclasses import dataclass

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from garblo.config import Settings


class StorageUnavailable(RuntimeError):
    """Object storage cannot be used in the current environment."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    content_type: str
    byte_size: int


class ObjectStorage:
    """Small S3-compatible adapter used by RustFS and test doubles."""

    def __init__(self, settings: Settings):
        if not settings.storage_is_configured:
            raise StorageUnavailable("RustFS/S3 configuration is incomplete")
        self.settings = settings
        self._client = self._new_client(settings.s3_endpoint_url)
        self._presign_client = (
            self._client
            if not settings.s3_presign_endpoint_url or settings.s3_presign_endpoint_url == settings.s3_endpoint_url
            else self._new_client(settings.s3_presign_endpoint_url)
        )

    def _new_client(self, endpoint_url: str | None):
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name=self.settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    @property
    def bucket(self) -> str:
        return self.settings.s3_bucket

    def put_bytes(self, key: str, body: bytes, content_type: str) -> StoredObject:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        return StoredObject(key=key, content_type=content_type, byte_size=len(body))

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self._client.create_bucket(Bucket=self.bucket)

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def presign_get(self, key: str) -> str:
        return self._presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.settings.s3_presign_seconds,
        )


def storage_key(user_id: str, asset_id: str, filename: str) -> str:
    safe_name = "".join(char for char in filename.lower() if char.isalnum() or char in {".", "-", "_"})
    suffix = safe_name.rsplit(".", 1)[-1] if "." in safe_name else "bin"
    return f"users/{user_id}/assets/{asset_id}.{suffix}"


def crop_storage_key(user_id: str, job_id: str, detection_index: int) -> str:
    return f"users/{user_id}/crops/{job_id}/{detection_index}.png"


def generated_storage_key(user_id: str, job_id: str) -> str:
    return f"users/{user_id}/generated/{job_id}.png"

