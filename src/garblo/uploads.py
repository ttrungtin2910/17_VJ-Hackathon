"""Reservation/lease bền vững, không giữ DB transaction qua network write S3."""

import hashlib
from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.asset_cleanup import TEMPORARY_INPUT
from garblo.models import MediaAsset, UploadOperation, User, new_id, now_utc
from garblo.storage import storage_key

UPLOAD_LEASE_SECONDS = 300


def _operation(db: Session, user_id: str, key: str) -> UploadOperation | None:
    return db.scalar(
        select(UploadOperation)
        .where(UploadOperation.user_id == user_id, UploadOperation.request_key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


def upload_with_key(
    db: Session,
    user_id: str,
    key: str,
    body: bytes,
    mime: str,
    asset_type: str,
    storage,
) -> MediaAsset:
    request_hash = hashlib.sha256(asset_type.encode("utf-8") + b"\0" + body).hexdigest()
    # Serialize cả lần đầu chưa có operation. Chỉ khóa trong các đoạn SQL ngắn.
    # NO KEY UPDATE vẫn serialize reservation nhưng tương thích KEY SHARE của
    # FK khi lượt trước publish asset; FOR UPDATE ở đây gây deadlock user ↔ op.
    db.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
    op = _operation(db, user_id, key)
    if op:
        if op.request_hash != request_hash:
            raise _conflict("idempotency_conflict", "Key đã dùng với ảnh hoặc loại asset khác")
        if op.status == "completed":
            return db.get(MediaAsset, op.asset_id)
        expires = op.lease_expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if op.status == "pending" and expires and expires > now_utc():
            raise _conflict(
                "upload_in_progress", "Upload đang xử lý; tra cứu lại trước khi thử lại"
            )
    else:
        asset_id = new_id()
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]
        op = UploadOperation(
            user_id=user_id,
            request_key=key,
            request_hash=request_hash,
            reserved_asset_id=asset_id,
            object_key=storage_key(user_id, asset_id, f"upload.{extension}"),
        )
        db.add(op)
    token = new_id()
    op.status, op.lease_token = "pending", token
    op.lease_expires_at = now_utc() + timedelta(seconds=UPLOAD_LEASE_SECONDS)
    asset_id, object_key = op.reserved_asset_id, op.object_key
    db.commit()
    try:
        # Cùng operation luôn dùng cùng object key và normalized bytes. Crash có thể
        # để lại orphan object; không tự xóa và không hứa exactly-once object write.
        storage.put_bytes(object_key, body, mime)
    except Exception as exc:
        db.rollback()
        op = _operation(db, user_id, key)
        if op and op.lease_token == token:
            op.status, op.lease_token, op.lease_expires_at = "failed", None, None
            db.commit()
        else:
            db.rollback()
        raise HTTPException(status_code=503, detail="Object storage không khả dụng") from exc
    op = _operation(db, user_id, key)
    if not op or op.lease_token != token or op.status != "pending":
        db.rollback()
        raise _conflict("upload_lease_lost", "Upload đã được lượt khác tiếp quản; tra cứu lại")
    asset = db.get(MediaAsset, asset_id)
    if not asset:
        asset = MediaAsset(
            id=asset_id,
            user_id=user_id,
            object_key=object_key,
            asset_type=asset_type[:40],
            mime_type=mime,
            checksum=hashlib.sha256(body).hexdigest(),
            byte_size=len(body),
            status="uploaded",
            metadata_json={"retention_policy": TEMPORARY_INPUT}
            if asset_type == "garment_photo"
            else {},
        )
        db.add(asset)
        db.flush()
    op.asset_id, op.status = asset.id, "completed"
    op.lease_token = op.lease_expires_at = None
    db.commit()
    db.refresh(asset)
    return asset
