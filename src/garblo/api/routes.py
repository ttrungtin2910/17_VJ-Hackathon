from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.config import Settings, get_settings
from garblo.database import get_db
from garblo.dependencies import get_current_user, require_ai_configuration
from garblo.models import ApprovalRequest, AiJob, ConsentRecord, Garment, Look, LookItem, Lookbook, MediaAsset, User, new_id
from garblo.schemas import (
    ApprovalDecisionRequest,
    ApprovalResponse,
    AssetDownloadResponse,
    AssetResponse,
    ConsentRequest,
    ConsentResponse,
    GarmentPatch,
    GarmentResponse,
    JobCreatedResponse,
    JobResponse,
    LoginRequest,
    LookResponse,
    LookbookCreateRequest,
    LookbookResponse,
    RegisterRequest,
    TokenResponse,
    UserResponse,
    VariationRequest,
)
from garblo.security import create_access_token, hash_password, verify_password
from garblo.storage import ObjectStorage, StorageUnavailable, storage_key


router = APIRouter()
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def storage_or_503(settings: Settings) -> ObjectStorage:
    try:
        return ObjectStorage(settings)
    except StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail="Object storage is not configured") from exc


def active_ai_consent(db: Session, user_id: str) -> bool:
    statement = (
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id, ConsentRecord.purpose == "ai_processing")
        .order_by(ConsentRecord.granted_at.desc())
        .limit(1)
    )
    consent = db.execute(statement).scalar_one_or_none()
    return bool(consent and consent.revoked_at is None)


def require_ai_consent(db: Session, user: User) -> None:
    if not active_ai_consent(db, user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active ai_processing consent is required before starting an AI job",
        )


def get_owned_asset_or_404(db: Session, user_id: str, asset_id: str) -> MediaAsset:
    asset = db.scalar(select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.user_id == user_id))
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


def get_owned_lookbook_or_404(db: Session, user_id: str, lookbook_id: str) -> Lookbook:
    lookbook = db.scalar(select(Lookbook).where(Lookbook.id == lookbook_id, Lookbook.user_id == user_id))
    if not lookbook:
        raise HTTPException(status_code=404, detail="Lookbook not found")
    return lookbook


@router.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DB) -> User:
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = User(email=email, display_name=payload.display_name.strip(), password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DB, settings: Settings = Depends(get_settings)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token, expires_at = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserResponse)
def current_profile(user: CurrentUser) -> User:
    return user


@router.post("/consents", response_model=ConsentResponse, status_code=status.HTTP_201_CREATED)
def grant_consent(payload: ConsentRequest, db: DB, user: CurrentUser) -> ConsentRecord:
    consent = ConsentRecord(user_id=user.id, purpose=payload.purpose, policy_version=payload.policy_version)
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent


@router.delete("/consents/{consent_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_consent(consent_id: str, db: DB, user: CurrentUser) -> Response:
    consent = db.scalar(
        select(ConsentRecord).where(ConsentRecord.id == consent_id, ConsentRecord.user_id == user.id)
    )
    if not consent:
        raise HTTPException(status_code=404, detail="Consent record not found")
    if not consent.revoked_at:
        consent.revoked_at = utcnow()
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assets", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    db: DB,
    user: CurrentUser,
    file: UploadFile = File(...),
    asset_type: str = Form("garment_photo"),
    settings: Settings = Depends(get_settings),
) -> MediaAsset:
    if file.content_type not in IMAGE_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG and WebP images are accepted")
    body = await file.read(settings.max_upload_bytes + 1)
    if not body:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(body) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the size limit")
    asset_id = new_id()
    object_key = storage_key(user.id, asset_id, file.filename or "upload.bin")
    asset = MediaAsset(
        id=asset_id,
        user_id=user.id,
        object_key=object_key,
        asset_type=asset_type[:40],
        mime_type=file.content_type,
        checksum=hashlib.sha256(body).hexdigest(),
        byte_size=len(body),
        status="uploaded",
    )
    storage = storage_or_503(settings)
    try:
        storage.put_bytes(object_key, body, file.content_type)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


@router.get("/assets", response_model=list[AssetResponse])
def list_assets(db: DB, user: CurrentUser) -> list[MediaAsset]:
    return list(db.scalars(select(MediaAsset).where(MediaAsset.user_id == user.id).order_by(MediaAsset.created_at.desc())))


@router.get("/assets/{asset_id}/download", response_model=AssetDownloadResponse)
def get_asset_download(asset_id: str, db: DB, user: CurrentUser, settings: Settings = Depends(get_settings)) -> AssetDownloadResponse:
    asset = get_owned_asset_or_404(db, user.id, asset_id)
    try:
        url = storage_or_503(settings).presign_get(asset.object_key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    return AssetDownloadResponse(url=url, expires_in_seconds=settings.s3_presign_seconds)


@router.post("/assets/{asset_id}/catalogize", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def catalogize_asset(
    asset_id: str,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    get_owned_asset_or_404(db, user.id, asset_id)
    job = AiJob(user_id=user.id, job_type="catalogue", input_ref={"asset_id": asset_id})
    db.add(job)
    db.commit()
    return JobCreatedResponse(job_id=job.id)


@router.get("/garments", response_model=list[GarmentResponse])
def list_garments(db: DB, user: CurrentUser) -> list[Garment]:
    return list(db.scalars(select(Garment).where(Garment.user_id == user.id).order_by(Garment.created_at.desc())))


@router.patch("/garments/{garment_id}", response_model=GarmentResponse)
def update_garment(garment_id: str, payload: GarmentPatch, db: DB, user: CurrentUser) -> Garment:
    garment = db.scalar(select(Garment).where(Garment.id == garment_id, Garment.user_id == user.id))
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(garment, field, value)
    db.commit()
    db.refresh(garment)
    return garment


@router.post("/lookbooks", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def create_lookbook(
    payload: LookbookCreateRequest,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    garment_ids = list(dict.fromkeys(payload.garment_ids))
    garments = list(
        db.scalars(select(Garment).where(Garment.user_id == user.id, Garment.id.in_(garment_ids)))
    )
    if len(garments) != len(garment_ids):
        raise HTTPException(status_code=422, detail="Every selected garment must belong to the current user")
    for optional_asset in (payload.pose_asset_id, payload.background_asset_id):
        if optional_asset:
            get_owned_asset_or_404(db, user.id, optional_asset)
    lookbook = Lookbook(
        user_id=user.id,
        title=payload.title,
        theme=payload.theme,
        occasion=payload.occasion,
        status="generating",
    )
    db.add(lookbook)
    db.flush()
    job = AiJob(
        user_id=user.id,
        job_type="lookbook",
        input_ref={
            "lookbook_id": lookbook.id,
            "garment_ids": garment_ids,
            "pose_asset_id": payload.pose_asset_id,
            "background_asset_id": payload.background_asset_id,
            "instruction": payload.instruction,
        },
    )
    db.add(job)
    db.commit()
    return JobCreatedResponse(job_id=job.id)


@router.get("/lookbooks", response_model=list[LookbookResponse])
def list_lookbooks(db: DB, user: CurrentUser) -> list[Lookbook]:
    return list(db.scalars(select(Lookbook).where(Lookbook.user_id == user.id).order_by(Lookbook.created_at.desc())))


@router.get("/lookbooks/{lookbook_id}/looks", response_model=list[LookResponse])
def list_looks(lookbook_id: str, db: DB, user: CurrentUser) -> list[Look]:
    get_owned_lookbook_or_404(db, user.id, lookbook_id)
    return list(db.scalars(select(Look).where(Look.lookbook_id == lookbook_id).order_by(Look.created_at.desc())))


@router.post("/looks/{look_id}/variations", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def create_variation(
    look_id: str,
    payload: VariationRequest,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    look = db.scalar(
        select(Look).join(Lookbook).where(Look.id == look_id, Lookbook.user_id == user.id)
    )
    if not look:
        raise HTTPException(status_code=404, detail="Look not found")
    garment_ids = list(db.scalars(select(LookItem.garment_id).where(LookItem.look_id == look.id)))
    job = AiJob(
        user_id=user.id,
        job_type="variation",
        input_ref={"lookbook_id": look.lookbook_id, "garment_ids": garment_ids, "instruction": payload.instruction},
    )
    db.add(job)
    db.commit()
    return JobCreatedResponse(job_id=job.id)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: DB, user: CurrentUser) -> AiJob:
    job = db.scalar(select(AiJob).where(AiJob.id == job_id, AiJob.user_id == user.id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: str,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    job = db.scalar(select(AiJob).where(AiJob.id == job_id, AiJob.user_id == user.id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried")
    job.status = "queued"
    job.error_code = None
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    db.commit()
    return JobCreatedResponse(job_id=job.id)


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(db: DB, user: CurrentUser, approval_status: str | None = None) -> list[ApprovalRequest]:
    statement = select(ApprovalRequest).where(ApprovalRequest.user_id == user.id)
    if approval_status:
        statement = statement.where(ApprovalRequest.status == approval_status)
    return list(db.scalars(statement.order_by(ApprovalRequest.requested_at.desc())))


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalResponse)
def decide_approval(
    approval_id: str, payload: ApprovalDecisionRequest, db: DB, user: CurrentUser
) -> ApprovalRequest:
    approval = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user.id)
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail="Approval request has already been decided")
    approval.status = payload.decision
    approval.decided_at = utcnow()
    approval.decision_payload = {"note": payload.note} if payload.note else {}
    if approval.subject_type == "look":
        look = db.get(Look, approval.subject_id)
        if not look:
            raise HTTPException(status_code=409, detail="Approval subject no longer exists")
        lookbook = get_owned_lookbook_or_404(db, user.id, look.lookbook_id)
        look.status = "accepted" if payload.decision == "accepted" else "rejected"
        lookbook.status = "approved" if payload.decision == "accepted" else "draft"
    db.commit()
    db.refresh(approval)
    return approval

