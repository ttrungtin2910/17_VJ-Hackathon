from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def document(**fields: Any) -> dict[str, Any]:
    timestamp = now_utc()
    return {"id": new_id(), "created_at": timestamp, "updated_at": timestamp, **fields}


def user_document(*, email: str, display_name: str, password_hash: str) -> dict[str, Any]:
    return document(
        email=email,
        display_name=display_name,
        password_hash=password_hash,
        style_profile={},
        social_opt_in=False,
    )


def consent_document(*, user_id: str, purpose: str, policy_version: str) -> dict[str, Any]:
    created = now_utc()
    return document(
        user_id=user_id,
        purpose=purpose,
        policy_version=policy_version,
        granted_at=created,
        revoked_at=None,
    )


def job_document(*, user_id: str, job_type: str, input_ref: dict[str, Any]) -> dict[str, Any]:
    return document(
        user_id=user_id,
        job_type=job_type,
        input_ref=input_ref,
        output_ref={},
        model_provider="pending",
        status="queued",
        attempts=0,
        error_code=None,
        error_message=None,
        error_retryable=False,
        started_at=None,
        completed_at=None,
        locked_at=None,
    )
