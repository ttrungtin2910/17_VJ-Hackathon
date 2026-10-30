from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.models import ConsentRecord


def active_ai_consent(db: Session, user_id: str) -> bool:
    consent = db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id, ConsentRecord.purpose == "ai_processing")
        .order_by(ConsentRecord.granted_at.desc(), ConsentRecord.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return bool(consent and consent.revoked_at is None)
