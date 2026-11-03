"""Dọn ảnh đầu vào sau commit; xóa object lặp lại an toàn khi worker phục hồi."""

from datetime import timedelta

from sqlalchemy import select

from garblo.models import AssetCleanup, MediaAsset, now_utc

TEMPORARY_INPUT = "temporary_catalogue_input"


def schedule_cleanup(db, asset):
    if asset and asset.metadata_json.get("retention_policy") == TEMPORARY_INPUT:
        if not db.get(AssetCleanup, asset.id):
            db.add(AssetCleanup(asset_id=asset.id))


def cleanup_next(db, storage):
    task = db.scalar(
        select(AssetCleanup)
        .where(AssetCleanup.completed_at.is_(None), AssetCleanup.next_attempt_at <= now_utc())
        .order_by(AssetCleanup.next_attempt_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not task:
        db.rollback()
        return False
    asset = db.get(MediaAsset, task.asset_id)
    task.attempts += 1
    try:
        storage.delete(asset.object_key)
    except Exception:
        task.next_attempt_at = now_utc() + timedelta(seconds=min(300, 2 ** min(task.attempts, 8)))
    else:
        task.completed_at = now_utc()
    db.commit()
    return True
