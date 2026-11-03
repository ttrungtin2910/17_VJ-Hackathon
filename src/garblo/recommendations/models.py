from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from garblo.database import Base
from garblo.models import JsonType, Timestamped, new_id


class RecommendationSession(Timestamped, Base):
    __tablename__ = "recommendation_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_recommendation_user_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    request_text: Mapped[str | None] = mapped_column(Text)
    retrieval_query: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    weights_snapshot: Mapped[dict[str, float]] = mapped_column(JsonType, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240))
    missing_categories: Mapped[list[str]] = mapped_column(JsonType, nullable=False)


class RecommendedOutfit(Timestamped, Base):
    __tablename__ = "recommended_outfits"
    __table_args__ = (UniqueConstraint("session_id", "rank", name="uq_recommended_outfit_rank"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_sessions.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    breakdown: Mapped[dict[str, float]] = mapped_column(JsonType, nullable=False)
    explanation: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    features_snapshot: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
    creation_features: Mapped[list[str]] = mapped_column(JsonType, nullable=False)


class RecommendedItem(Timestamped, Base):
    __tablename__ = "recommended_items"
    __table_args__ = (
        UniqueConstraint("outfit_id", "garment_id", name="uq_recommended_item_garment"),
        UniqueConstraint("outfit_id", "role", name="uq_recommended_item_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    outfit_id: Mapped[str] = mapped_column(
        ForeignKey("recommended_outfits.id", ondelete="CASCADE"), index=True
    )
    garment_id: Mapped[str] = mapped_column(
        ForeignKey("garments.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)


class RecommendationFeedback(Timestamped, Base):
    __tablename__ = "recommendation_feedback"
    __table_args__ = (
        UniqueConstraint("user_id", "outfit_id", name="uq_recommendation_feedback_user_outfit"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    outfit_id: Mapped[str] = mapped_column(
        ForeignKey("recommended_outfits.id", ondelete="CASCADE"), index=True
    )
    signal: Mapped[str] = mapped_column(String(10), nullable=False)
    features_snapshot: Mapped[list[str]] = mapped_column(JsonType, nullable=False)
