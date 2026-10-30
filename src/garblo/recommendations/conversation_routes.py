"""Enqueue intent editing riêng, không mở rộng endpoint variation lookbook."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.config import Settings, get_settings
from garblo.consent import active_ai_consent
from garblo.database import get_db
from garblo.dependencies import get_current_user
from garblo.models import AiJob, User
from garblo.recommendations.conversation import MessageRequest, validate_source


class MessageJobResponse(BaseModel):
    job_id: str
    status: str


router = APIRouter(prefix="/recommendations", tags=["recommendations"])
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post(
    "/{session_id}/outfits/{outfit_id}/messages",
    status_code=202,
    response_model=MessageJobResponse,
)
def post_message(
    session_id: str,
    outfit_id: str,
    payload: MessageRequest,
    db: DB,
    user: CurrentUser,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    settings: Settings = Depends(get_settings),
) -> MessageJobResponse:
    if not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key không được để trống.")
    user_id = user.id
    # Một khóa user dùng chung serialize cả trường hợp chưa có job cùng key.
    db.execute(select(User.id).where(User.id == user_id).with_for_update())
    request = {**payload.model_dump(), "session_id": session_id, "outfit_id": outfit_id}
    # Danh sách là tập hợp; thứ tự locks/excludes không thay đổi ý nghĩa request.
    request["locked_garment_ids"] = sorted(payload.locked_garment_ids)
    request["excluded_garment_ids"] = sorted(payload.excluded_garment_ids)
    digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    existing = db.scalar(
        select(AiJob).where(
            AiJob.user_id == user_id,
            AiJob.job_type == "recommendation_edit",
            AiJob.input_ref["idempotency_key"].as_string() == idempotency_key,
        )
    )
    if existing:
        if existing.input_ref.get("request_hash") != digest:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "idempotency_conflict",
                    "message": "Khóa đã dùng với yêu cầu khác.",
                },
            )
        return MessageJobResponse(job_id=existing.id, status=existing.status)
    if not active_ai_consent(db, user_id):
        raise HTTPException(status_code=409, detail="Cần đồng ý ai_processing trước khi dùng AI.")
    validate_source(db, user_id, session_id, outfit_id, payload)
    model = getattr(settings, "stylist_intent_model", None)
    if not model or not model.strip() or not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_not_configured", "message": "Intent model chưa cấu hình."},
        )
    snapshot = {
        **request,
        "idempotency_key": idempotency_key,
        "request_hash": digest,
        "provider": "openai",
        "model": model,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": "stylist-edit-v1",
        "schema_version": 1,
        "max_calls": getattr(settings, "stylist_intent_max_calls", 3),
        "max_output_tokens": 1024,
        "token_budget": 50000,
        "call_token_reservation": 16384,
        "deadline_seconds": 300,
        "supports_temperature": settings.catalogue_supports_temperature,
        "native_structured_output": getattr(
            settings, "stylist_intent_native_structured_output", False
        ),
    }
    job = AiJob(
        user_id=user_id,
        job_type="recommendation_edit",
        model_provider="openai",
        input_ref=snapshot,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return MessageJobResponse(job_id=job.id, status=job.status)
