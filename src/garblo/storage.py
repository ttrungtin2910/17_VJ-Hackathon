from __future__ import annotations

from dataclasses import dataclass

import boto3
from botocore.client import Config

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
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    @property
    def bucket(self) -> str:
        return self.settings.s3_bucket

    def put_bytes(self, key: str, body: bytes, content_type: str) -> StoredObject:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        return StoredObject(key=key, content_type=content_type, byte_size=len(body))

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def presign_get(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.settings.s3_presign_seconds,
        )


def storage_key(user_id: str, asset_id: str, filename: str) -> str:
    safe_name = "".join(char for char in filename.lower() if char.isalnum() or char in {".", "-", "_"})
    suffix = safe_name.rsplit(".", 1)[-1] if "." in safe_name else "bin"
    return f"users/{user_id}/assets/{asset_id}.{suffix}"

