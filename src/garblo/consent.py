from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.models import ConsentRecord


def latest_consent(db: Session, user_id: str, purpose: str) -> ConsentRecord | None:
    return db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id, ConsentRecord.purpose == purpose)
        .order_by(
            ConsentRecord.granted_at.desc(),
            ConsentRecord.created_at.desc(),
            ConsentRecord.id.desc(),
        )
        .limit(1)
        .execution_options(populate_existing=True)
    )


def active_ai_consent(db: Session, user_id: str) -> bool:
    consent = latest_consent(db, user_id, "ai_processing")
    return bool(consent and consent.revoked_at is None)
