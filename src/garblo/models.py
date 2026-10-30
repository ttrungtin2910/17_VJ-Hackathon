from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from garblo.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now().astimezone()


JsonType = JSON().with_variant(JSONB(), "postgresql")


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False
    )


class User(Timestamped, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    style_profile: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    social_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ConsentRecord(Timestamped, Base):
    __tablename__ = "consent_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(80), index=True)
    policy_version: Mapped[str] = mapped_column(String(80))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaAsset(Timestamped, Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, default=dict, nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), default="uploaded", nullable=False)
    parent_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )


class Garment(Timestamped, Base):
    __tablename__ = "garments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    category: Mapped[str | None] = mapped_column(String(80))
    color: Mapped[str | None] = mapped_column(String(80))
    material: Mapped[str | None] = mapped_column(String(80))
    season: Mapped[str | None] = mapped_column(String(80))
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    wear_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_worn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    catalog_status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    component_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT")
    )


class Lookbook(Timestamped, Base):
    __tablename__ = "lookbooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    theme: Mapped[str | None] = mapped_column(String(120))
    occasion: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    cover_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )


class Look(Timestamped, Base):
    __tablename__ = "looks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_jobs.id", ondelete="RESTRICT"), unique=True
    )
    lookbook_id: Mapped[str] = mapped_column(
        ForeignKey("lookbooks.id", ondelete="CASCADE"), index=True
    )
    generated_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    generation_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_reference: Mapped[str | None] = mapped_column(String(160))
    cmax_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(40), default="preview", nullable=False)


class LookItem(Timestamped, Base):
    __tablename__ = "look_items"
    __table_args__ = (UniqueConstraint("look_id", "garment_id", name="uq_look_items_look_garment"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    look_id: Mapped[str] = mapped_column(ForeignKey("looks.id", ondelete="CASCADE"), index=True)
    garment_id: Mapped[str] = mapped_column(
        ForeignKey("garments.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str | None] = mapped_column(String(80))
    layer_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AiJob(Timestamped, Base):
    __tablename__ = "ai_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    input_ref: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    output_ref: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    model_provider: Mapped[str] = mapped_column(String(80), default="openai", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_token: Mapped[str | None] = mapped_column(String(36))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    execution_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GarmentCandidate(Timestamped, Base):
    __tablename__ = "garment_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_index", name="uq_candidates_job_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("ai_jobs.id", ondelete="RESTRICT"), index=True)
    candidate_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    original_attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    review_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    component_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    garment_id: Mapped[str | None] = mapped_column(
        ForeignKey("garments.id", ondelete="RESTRICT"), unique=True
    )


class AiCallAttempt(Timestamped, Base):
    """Một reservation bền vững cho mỗi request; reserved có thể đã phát sinh phí."""

    __tablename__ = "ai_call_attempts"
    __table_args__ = (UniqueConstraint("job_id", "call_index", name="uq_ai_calls_job_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("ai_jobs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="reserved", nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))

    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    validation_status: Mapped[str | None] = mapped_column(String(30))


class ApprovalRequest(Timestamped, Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_type: Mapped[str] = mapped_column(String(40), index=True)
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True, nullable=False)
    decision_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StyleSignal(Timestamped, Base):
    __tablename__ = "style_signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    look_id: Mapped[str] = mapped_column(ForeignKey("looks.id", ondelete="CASCADE"), index=True)
    signal: Mapped[str] = mapped_column(String(40), nullable=False)
    weight: Mapped[float] = mapped_column(Numeric(6, 3), default=1, nullable=False)


class TasteMatch(Timestamped, Base):
    __tablename__ = "taste_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    matched_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    similarity_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="new", nullable=False)
