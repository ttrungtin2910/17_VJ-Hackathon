from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["top", "bottom", "shoes", "dress", "outerwear", "accessory"]
EventId = Literal[
    "work", "school", "interview", "presentation", "date", "wedding", "casual", "other"
]
StyleId = Literal[
    "formal", "smart-casual", "casual", "minimal", "streetwear", "elegant", "sporty", "other"
]
WeatherId = Literal["sunny", "mild", "humid", "rainy", "cold", "snowy", "other"]
TransportId = Literal["walk", "metro", "bus", "motorbike", "car", "bicycle", "taxi"]
Identifier = Annotated[str, Field(min_length=1, max_length=36)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Choice(StrictModel):
    custom: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def custom_required(self) -> Self:
        if self.id == "other" and not self.custom:
            raise ValueError("Vui lòng mô tả lựa chọn Khác.")
        return self


class EventChoice(Choice):
    id: EventId


class StyleChoice(Choice):
    id: StyleId
    source: Literal["suggestion", "manual"]


class WeatherChoice(Choice):
    id: WeatherId


class RecommendationContext(StrictModel):
    version: Literal[1]
    event: EventChoice
    style: StyleChoice
    weather: WeatherChoice | None = None
    transports: list[TransportId] = Field(default_factory=list, max_length=7)

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("version phải là số nguyên 1")
        return value

    @field_validator("transports")
    @classmethod
    def unique_transports(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Di chuyển không được trùng lặp.")
        return value


class CreateSessionRequest(StrictModel):
    context: RecommendationContext
    location: str | None = Field(default=None, max_length=200)
    prompt: str | None = Field(default=None, min_length=1, max_length=1000)


class EditConstraints(StrictModel):
    """Chỉ áp dụng cho món thay thế; không diễn dịch prompt tùy ý."""

    colors: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=10
    )
    materials: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=10
    )
    styles: list[StyleId] = Field(default_factory=list, max_length=8)


class EditRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    operation: Literal["replace"]
    target_garment_id: Identifier
    replacement_garment_id: Identifier | None = None
    locked_garment_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    excluded_garment_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    constraints: EditConstraints | None = None

    @model_validator(mode="after")
    def distinct_ids(self) -> Self:
        for values in (self.locked_garment_ids, self.excluded_garment_ids):
            if len(values) != len(set(values)):
                raise ValueError("Danh sách ID không được trùng lặp.")
        return self


class FeedbackRequest(StrictModel):
    signal: Literal["like", "dislike"]


class GarmentSnapshot(StrictModel):
    garment_id: str
    role: Role
    category: str
    color: str | None
    material: str | None
    season: str | None
    tags: list[str]
    details: dict
    wear_count: int
    last_worn_at: str | None
    source_asset_id: str
    component_asset_id: str | None


class ScoreBreakdown(StrictModel):
    context: float
    style: float
    color: float
    weather: float
    repetition: float
    personalization: float


class OutfitResponse(StrictModel):
    id: str
    session_id: str
    rank: int
    revision: int
    items: list[GarmentSnapshot]
    score: float
    breakdown: ScoreBreakdown
    explanation: list[str]
    features_snapshot: list[str]
    feedback: Literal["like", "dislike"] | None = None


class SessionResponse(StrictModel):
    id: str
    context: RecommendationContext
    location: str | None
    prompt: str | None = None
    retrieval_query: str | None = None
    engine_version: str
    weights_snapshot: dict[str, float]
    outfits: list[OutfitResponse]
    reason: str | None
    missing_categories: list[str]
    score_notice: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value):
        # SQLite test loại tzinfo; PostgreSQL giữ timestamp with time zone.
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )


class FeedbackResponse(StrictModel):
    outfit_id: str
    signal: Literal["like", "dislike"]
