from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from garblo.ai_providers.contracts import GarmentAttributes, ShortText
from garblo.catalogue.contracts import ComponentDetails


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class UserResponse(APIModel):
    id: str
    email: EmailStr
    display_name: str
    social_opt_in: bool
    created_at: datetime


class ConsentRequest(BaseModel):
    purpose: str = Field(min_length=2, max_length=80)
    policy_version: str = Field(min_length=1, max_length=80)


class ConsentResponse(APIModel):
    id: str
    purpose: str
    policy_version: str
    granted_at: datetime
    revoked_at: datetime | None


class AssetResponse(APIModel):
    id: str
    asset_type: str
    mime_type: str
    checksum: str
    byte_size: int
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    status: str
    created_at: datetime
    parent_asset_id: str | None


class AssetDownloadResponse(BaseModel):
    url: str
    expires_in_seconds: int


class GarmentPatch(BaseModel):
    category: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=80)
    material: str | None = Field(default=None, max_length=80)
    season: str | None = Field(default=None, max_length=80)
    tags: list[ShortText] = Field(default_factory=list, max_length=20)
    catalog_status: str | None = Field(default=None, max_length=40)
    details: ComponentDetails | None = None

    @field_validator("catalog_status")
    @classmethod
    def valid_status(cls, value):
        if value not in {"active", "archived"}:
            raise ValueError("catalog_status phải là active hoặc archived")
        return value


class CatalogueRequest(BaseModel):
    vlm_provider: Literal["openai", "fpt"] = "openai"


class CandidatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None = Field(default=None, max_length=80, min_length=1)
    color: str | None = Field(default=None, max_length=80, min_length=1)
    material: str | None = Field(default=None, max_length=80, min_length=1)
    season: str | None = Field(default=None, max_length=80, min_length=1)
    tags: list[ShortText] = Field(default_factory=list, max_length=20)
    details: ComponentDetails | None = None
    status: Literal["draft", "rejected"] | None = None


class CandidateResponse(APIModel):
    id: str
    asset_id: str
    job_id: str
    status: str
    attributes: GarmentAttributes = Field(validation_alias="attributes_json")
    garment_id: str | None
    created_at: datetime
    updated_at: datetime
    schema_version: int
    details: ComponentDetails = Field(validation_alias="details_json")
    review: dict[str, Any] = Field(validation_alias="review_json")
    component_asset_id: str | None


class CommitCandidatesRequest(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=20)


class CommitCandidatesResponse(BaseModel):
    garment_ids: list[str]


class GarmentResponse(APIModel):
    id: str
    source_asset_id: str
    category: str | None
    color: str | None
    material: str | None
    season: str | None
    tags: list[str]
    wear_count: int
    catalog_status: str
    created_at: datetime
    details: ComponentDetails = Field(validation_alias="details_json")
    component_asset_id: str | None


class LookbookCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    garment_ids: list[str] = Field(min_length=1, max_length=20)
    theme: str | None = Field(default=None, max_length=120)
    occasion: str | None = Field(default=None, max_length=120)
    pose_asset_id: str | None = None
    background_asset_id: str | None = None
    instruction: str | None = Field(default=None, max_length=1000)
    preset_id: str | None = Field(default=None, max_length=80)


class LookbookResponse(APIModel):
    id: str
    title: str
    theme: str | None
    occasion: str | None
    status: str
    created_at: datetime


class LookResponse(APIModel):
    id: str
    lookbook_id: str
    generated_asset_id: str | None
    generation_prompt: str
    model_reference: str | None
    status: str
    created_at: datetime
    garment_ids: list[str] = Field(default_factory=list)
    approval_id: str | None = None


class JobResponse(APIModel):
    id: str
    job_type: str
    status: str
    output_ref: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    model_provider: str
    model: str | None = None
    attempts: int
    next_attempt_at: datetime | None


class JobCreatedResponse(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"


class AICallResponse(APIModel):
    id: str
    call_index: int
    worker_attempt: int
    stage: str
    provider: str
    model: str
    prompt_version: str | None
    schema_version: int
    status: str
    usage: dict[str, int]
    latency_ms: int | None
    error_code: str | None
    reserved_tokens: int
    validation_status: str | None


class VariationRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=1000)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["accepted", "rejected"]
    note: str | None = Field(default=None, max_length=500)


class ApprovalResponse(APIModel):
    id: str
    subject_type: str
    subject_id: str
    action: str
    status: str
    requested_at: datetime
    decided_at: datetime | None


class LookFeedbackRequest(BaseModel):
    signal: Literal["like", "dislike"]


class LookFeedbackResponse(APIModel):
    id: str
    look_id: str
    signal: str
