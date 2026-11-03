"""Endpoint stylist một lượt, snapshot và idempotency theo user."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from garblo.config import Settings, get_settings
from garblo.consent import active_ai_consent
from garblo.models import AiJob, User
from garblo.recommendations.conversation import validate_source
from garblo.recommendations.conversation_routes import DB, CurrentUser, MessageJobResponse
from garblo.recommendations.stylist import StylistRequest

router = APIRouter(prefix="/stylist", tags=["stylist"])


@router.post("/messages", status_code=202, response_model=MessageJobResponse)
def post_stylist_message(
    payload: StylistRequest,
    db: DB,
    user: CurrentUser,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    settings: Settings = Depends(get_settings),
) -> MessageJobResponse:
    if not idempotency_key.strip():
        raise HTTPException(422, detail="Idempotency-Key không được để trống.")
    user_id = user.id
    request = payload.model_dump(mode="json")
    for field in ("locked_garment_ids", "excluded_garment_ids"):
        request[field] = sorted(request[field])
    digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    db.execute(select(User.id).where(User.id == user_id).with_for_update())
    existing = db.scalar(
        select(AiJob).where(
            AiJob.user_id == user_id,
            AiJob.job_type == "stylist",
            AiJob.input_ref["idempotency_key"].as_string() == idempotency_key,
        )
    )
    if existing:
        if existing.input_ref.get("request_hash") != digest:
            raise HTTPException(
                409,
                detail={
                    "code": "idempotency_conflict",
                    "message": "Khóa đã dùng với yêu cầu khác.",
                },
            )
        return MessageJobResponse(job_id=existing.id, status=existing.status)
    if not active_ai_consent(db, user_id):
        raise HTTPException(
            409, detail={"code": "consent_required", "message": "Cần đồng ý xử lý AI."}
        )
    if payload.outfit_id:
        validate_source(db, user_id, payload.session_id, payload.outfit_id, payload)
    if not settings.stylist_intent_model or not settings.openai_api_key:
        raise HTTPException(
            503, detail={"code": "provider_not_configured", "message": "Stylist chưa cấu hình."}
        )
    job = AiJob(
        user_id=user_id,
        job_type="stylist",
        model_provider="openai",
        input_ref={
            "request": request,
            "request_hash": digest,
            "idempotency_key": idempotency_key,
            "provider": "openai",
            "model": settings.stylist_intent_model,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "prompt_version": "stylist-turn-v1",
            "schema_version": 1,
            "max_calls": settings.stylist_intent_max_calls,
            "max_output_tokens": 1024,
            "token_budget": 50000,
            "call_token_reservation": 16384,
            "deadline_seconds": 300,
            "supports_temperature": settings.catalogue_supports_temperature,
            "native_structured_output": settings.stylist_intent_native_structured_output,
        },
    )
    db.add(job)
    db.commit()
    return MessageJobResponse(job_id=job.id, status=job.status)
