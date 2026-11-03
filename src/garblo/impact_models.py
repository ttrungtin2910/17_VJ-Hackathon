"""Metadata lịch sử lượt mặc; không tham chiếu ảnh hoặc outfit bằng FK."""

from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from garblo.database import Base
from garblo.models import JsonType, Timestamped, new_id


class WearEvent(Timestamped, Base):
    __tablename__ = "wear_events"
    __table_args__ = (
        UniqueConstraint("user_id", "outfit_id", "worn_on", name="uq_wear_user_outfit_day"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(36))
    outfit_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int]
    item_ids: Mapped[list[str]] = mapped_column(JsonType)
    worn_on: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(100))


class ImpactProgress(Base):
    __tablename__ = "impact_progress"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict] = mapped_column(JsonType, default=dict)
