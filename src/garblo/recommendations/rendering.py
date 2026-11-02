"""Render ảnh cho một revision; dùng lại job khi refresh hoặc mất response."""

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.ai import AIProviderError
from garblo.config import Settings
from garblo.consent import active_ai_consent
from garblo.jobs import new_snapshot
from garblo.models import AiJob, ApprovalRequest, Garment, Lookbook
from garblo.presets import PRESETS
from garblo.recommendations import service
from garblo.recommendations.models import RecommendedItem


class RenderRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class RenderResponse(BaseModel):
    job_id: str
    status: str
    error_code: str | None
    generated_asset_id: str | None
    approval_id: str | None
    approval_status: str | None


def find_job(db: Session, user_id: str, outfit_id: str, revision: int) -> AiJob | None:
    return db.scalar(
        select(AiJob).where(
            AiJob.user_id == user_id,
            AiJob.job_type == "lookbook",
            AiJob.input_ref["outfit_id"].as_string() == outfit_id,
            AiJob.input_ref["outfit_revision"].as_integer() == revision,
        )
    )


def response(db: Session, job: AiJob) -> RenderResponse:
    output = job.output_ref or {}
    approval = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == output.get("approval_id"),
            ApprovalRequest.user_id == job.user_id,
        )
    )
    return RenderResponse(
        job_id=job.id,
        status=job.status,
        error_code=job.error_code,
        generated_asset_id=output.get("generated_asset_id"),
        approval_id=approval.id if approval else None,
        approval_status=approval.status if approval else None,
    )


def get_render(db: Session, user_id: str, session_id: str, outfit_id: str):
    outfit = service.get_owned_outfit(db, user_id, session_id, outfit_id)
    job = find_job(db, user_id, outfit.id, outfit.revision)
    return response(db, job) if job else None


def start_render(
    db: Session,
    user_id: str,
    session_id: str,
    outfit_id: str,
    payload: RenderRequest,
    settings: Settings,
) -> RenderResponse:
    service.lock_user(db, user_id)
    outfit = service.get_owned_outfit(db, user_id, session_id, outfit_id, lock=True)
    if outfit.revision != payload.expected_revision:
        service.fail(409, "revision_conflict", "Bộ phối đã thay đổi; vui lòng tải lại.")
    existing = find_job(db, user_id, outfit.id, outfit.revision)
    if existing:
        result = response(db, existing)
        db.commit()
        return result
    if not active_ai_consent(db, user_id):
        service.fail(409, "consent_required", "Cần đồng ý xử lý AI trước khi tạo ảnh.")
    try:
        snapshot = new_snapshot(settings, "openai", "lookbook")
    except AIProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc
    ids = list(
        db.scalars(
            select(RecommendedItem.garment_id).where(
                RecommendedItem.outfit_id == outfit.id,
            )
        )
    )
    garments = list(
        db.scalars(
            select(Garment)
            .where(
                Garment.user_id == user_id,
                Garment.id.in_(ids),
                Garment.catalog_status == "active",
            )
            .order_by(Garment.id)
            .with_for_update()
        )
    )
    if not ids or len(garments) != len(set(ids)):
        service.fail(409, "wardrobe_changed", "Món đồ không còn hoạt động; hãy tạo phiên mới.")
    session = service.get_owned_session(db, user_id, session_id)
    event = session.context_json["event"]["id"]
    preset = {
        "work": "office",
        "interview": "office",
        "presentation": "office",
        "date": "evening",
        "wedding": "evening",
        "casual": "weekend",
    }.get(event, "everyday")
    book = Lookbook(
        user_id=user_id, title="Ảnh bộ phối", theme=preset, occasion=preset, status="generating"
    )
    db.add(book)
    db.flush()
    job = AiJob(
        user_id=user_id,
        job_type="lookbook",
        model_provider="openai",
        input_ref={
            **snapshot,
            "lookbook_id": book.id,
            "garment_ids": ids,
            "preset_id": preset,
            "instruction": PRESETS[preset],
            "outfit_id": outfit.id,
            "outfit_revision": outfit.revision,
            "session_id": session_id,
        },
    )
    db.add(job)
    db.commit()
    return response(db, job)
