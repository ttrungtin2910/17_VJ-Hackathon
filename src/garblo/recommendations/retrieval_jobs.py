from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.config import Settings
from garblo.consent import active_ai_consent
from garblo.models import AiJob, Garment
from garblo.recommendations.retrieval import PROFILE_VERSION, profile_text


def refresh_profile(db: Session, garment: Garment, settings: Settings) -> None:
    previous = garment.retrieval_json or {}
    text = profile_text(garment)
    model = settings.openai_embedding_model
    if previous.get("text") != text or previous.get("version") != PROFILE_VERSION:
        garment.retrieval_json = {
            "version": PROFILE_VERSION,
            "text": text,
            "embedding": None,
            "embedding_model": model,
            "status": "pending",
        }
    if not text or not model or not settings.openai_api_key or not active_ai_consent(db, garment.user_id):
        return
    profile = garment.retrieval_json or {}
    if profile.get("status") == "ready" and profile.get("embedding_model") == model:
        return
    existing = db.scalar(
        select(AiJob.id).where(
            AiJob.user_id == garment.user_id,
            AiJob.job_type == "garment_embedding",
            AiJob.status.in_(["queued", "running"]),
            AiJob.input_ref["garment_id"].as_string() == garment.id,
        )
    )
    if existing:
        return
    db.add(
        AiJob(
            user_id=garment.user_id,
            job_type="garment_embedding",
            model_provider="openai",
            input_ref={"garment_id": garment.id, "model": model, "profile_version": PROFILE_VERSION},
        )
    )
