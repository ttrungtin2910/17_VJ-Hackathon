from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class AssetDownloadResponse(BaseModel):
    url: str
    expires_in_seconds: int


class GarmentPatch(BaseModel):
    category: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=80)
    material: str | None = Field(default=None, max_length=80)
    season: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = None
    catalog_status: str | None = Field(default=None, max_length=40)


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
    original_asset_id: str | None = None
    crop_asset_id: str | None = None
    detection: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    vector_status: str | None = None


class UploadBatchResponse(APIModel):
    id: str
    status: str
    asset_ids: list[str]
    job_ids: list[str]
    total_count: int
    processed_count: int
    failed_count: int
    created_at: datetime


class WardrobeSearchRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=2_000)


class WardrobeSearchResponse(APIModel):
    id: str
    prompt: str
    status: str
    result: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime


class WardrobeSearchCreatedResponse(BaseModel):
    search_id: str
    job_id: str
    status: Literal["queued"] = "queued"


class LookbookCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    garment_ids: list[str] = Field(min_length=1, max_length=20)
    theme: str | None = Field(default=None, max_length=120)
    occasion: str | None = Field(default=None, max_length=120)
    pose_asset_id: str | None = None
    background_asset_id: str | None = None
    instruction: str | None = Field(default=None, max_length=1000)


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


class JobResponse(APIModel):
    id: str
    job_type: str
    status: str
    output_ref: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class JobCreatedResponse(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"


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

