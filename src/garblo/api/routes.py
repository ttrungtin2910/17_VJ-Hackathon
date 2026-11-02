from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from garblo.config import Settings, get_settings
from garblo.database import get_db
from garblo.dependencies import get_current_user, require_ai_configuration, require_recommendation_configuration
from garblo.models import consent_document, document, job_document, new_id, user_document
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
    UploadBatchResponse,
    WardrobeSearchCreatedResponse,
    WardrobeSearchRequest,
    WardrobeSearchResponse,
)
from garblo.security import create_access_token, hash_password, verify_password
from garblo.storage import ObjectStorage, StorageUnavailable, storage_key


router = APIRouter()
DB = Annotated[Database, Depends(get_db)]
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def storage_or_503(settings: Settings) -> ObjectStorage:
    try:
        return ObjectStorage(settings)
    except StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail="Object storage is not configured") from exc


def active_ai_consent(db: Database, user_id: str) -> bool:
    consent = db.consent_records.find_one(
        {"user_id": user_id, "purpose": "ai_processing"},
        sort=[("granted_at", DESCENDING)],
    )
    return bool(consent and consent.get("revoked_at") is None)


def require_ai_consent(db: Database, user: dict[str, Any]) -> None:
    if not active_ai_consent(db, user["id"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active ai_processing consent is required before starting an AI job",
        )


def get_owned_asset_or_404(db: Database, user_id: str, asset_id: str) -> dict[str, Any]:
    asset = db.media_assets.find_one({"id": asset_id, "user_id": user_id})
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


def get_owned_lookbook_or_404(db: Database, user_id: str, lookbook_id: str) -> dict[str, Any]:
    lookbook = db.lookbooks.find_one({"id": lookbook_id, "user_id": user_id})
    if not lookbook:
        raise HTTPException(status_code=404, detail="Lookbook not found")
    return lookbook


@router.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DB) -> dict[str, Any]:
    user = user_document(
        email=payload.email.lower(),
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    try:
        db.users.insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Email is already registered") from exc
    return user


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DB, settings: Settings = Depends(get_settings)) -> TokenResponse:
    user = db.users.find_one({"email": payload.email.lower()})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token, expires_at = create_access_token(user["id"], settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserResponse)
def current_profile(user: CurrentUser) -> dict[str, Any]:
    return user


@router.post("/consents", response_model=ConsentResponse, status_code=status.HTTP_201_CREATED)
def grant_consent(payload: ConsentRequest, db: DB, user: CurrentUser) -> dict[str, Any]:
    consent = consent_document(user_id=user["id"], purpose=payload.purpose, policy_version=payload.policy_version)
    db.consent_records.insert_one(consent)
    return consent


@router.delete("/consents/{consent_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_consent(consent_id: str, db: DB, user: CurrentUser) -> Response:
    consent = db.consent_records.find_one({"id": consent_id, "user_id": user["id"]})
    if not consent:
        raise HTTPException(status_code=404, detail="Consent record not found")
    if consent.get("revoked_at") is None:
        db.consent_records.update_one(
            {"_id": consent["_id"]},
            {"$set": {"revoked_at": utcnow(), "updated_at": utcnow()}},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assets", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    db: DB,
    user: CurrentUser,
    file: UploadFile = File(...),
    asset_type: str = Form("garment_photo"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if file.content_type not in IMAGE_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG and WebP images are accepted")
    body = await file.read(settings.max_upload_bytes + 1)
    if not body:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(body) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the size limit")
    asset_id = new_id()
    object_key = storage_key(user["id"], asset_id, file.filename or "upload.bin")
    asset = document(
        id=asset_id,
        user_id=user["id"],
        object_key=object_key,
        asset_type=asset_type[:40],
        mime_type=file.content_type,
        checksum=hashlib.sha256(body).hexdigest(),
        byte_size=len(body),
        metadata_json={},
        status="uploaded",
    )
    storage = storage_or_503(settings)
    try:
        storage.put_bytes(object_key, body, file.content_type)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    db.media_assets.insert_one(asset)
    return asset


@router.post("/uploads", response_model=UploadBatchResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_assets(
    db: DB,
    user: CurrentUser,
    files: list[UploadFile] = File(...),
    asset_type: str = Form("garment_photo"),
    settings: Settings = Depends(require_ai_configuration),
) -> dict[str, Any]:
    """Store a bounded batch then enqueue one idempotent ingestion job per source image."""
    require_ai_consent(db, user)
    if not files:
        raise HTTPException(status_code=422, detail="At least one image is required")
    if len(files) > settings.max_upload_batch_files:
        raise HTTPException(status_code=413, detail="Uploaded batch exceeds the file limit")
    prepared: list[tuple[UploadFile, bytes]] = []
    total_bytes = 0
    for file in files:
        if file.content_type not in IMAGE_MIME_TYPES:
            raise HTTPException(status_code=415, detail="Only JPEG, PNG and WebP images are accepted")
        body = await file.read(settings.max_upload_bytes + 1)
        if not body:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")
        if len(body) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Uploaded file exceeds the size limit")
        total_bytes += len(body)
        if total_bytes > settings.max_upload_batch_bytes:
            raise HTTPException(status_code=413, detail="Uploaded batch exceeds the total size limit")
        prepared.append((file, body))

    storage = storage_or_503(settings)
    batch = document(
        user_id=user["id"], status="queued", asset_ids=[], job_ids=[], total_count=len(prepared),
        processed_count=0, failed_count=0,
    )
    db.upload_batches.insert_one(batch)
    asset_ids: list[str] = []
    job_ids: list[str] = []
    try:
        for file, body in prepared:
            asset_id = new_id()
            asset = document(
                id=asset_id, user_id=user["id"], object_key=storage_key(user["id"], asset_id, file.filename or "upload.bin"),
                asset_type=asset_type[:40], mime_type=file.content_type, checksum=hashlib.sha256(body).hexdigest(),
                byte_size=len(body), metadata_json={}, status="queued_for_ingestion", upload_id=batch["id"],
            )
            storage.put_bytes(asset["object_key"], body, asset["mime_type"])
            db.media_assets.insert_one(asset)
            job = job_document(
                user_id=user["id"], job_type="ingest_asset", input_ref={"asset_id": asset_id, "upload_id": batch["id"]}
            )
            job["model_provider"] = "local_ml+openai"
            db.ai_jobs.insert_one(job)
            asset_ids.append(asset_id)
            job_ids.append(job["id"])
    except Exception as exc:
        db.upload_batches.update_one(
            {"id": batch["id"]},
            {"$set": {"status": "failed", "error_message": "Upload persistence failed", "updated_at": utcnow()}},
        )
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    batch.update({"asset_ids": asset_ids, "job_ids": job_ids})
    db.upload_batches.update_one(
        {"id": batch["id"]}, {"$set": {"asset_ids": asset_ids, "job_ids": job_ids, "updated_at": utcnow()}}
    )
    return batch


@router.get("/uploads/{upload_id}", response_model=UploadBatchResponse)
def get_upload_batch(upload_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    batch = db.upload_batches.find_one({"id": upload_id, "user_id": user["id"]})
    if not batch:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    return batch


@router.get("/assets", response_model=list[AssetResponse])
def list_assets(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    return list(db.media_assets.find({"user_id": user["id"]}).sort("created_at", DESCENDING))


@router.get("/assets/{asset_id}/download", response_model=AssetDownloadResponse)
def get_asset_download(asset_id: str, db: DB, user: CurrentUser, settings: Settings = Depends(get_settings)) -> AssetDownloadResponse:
    asset = get_owned_asset_or_404(db, user["id"], asset_id)
    try:
        url = storage_or_503(settings).presign_get(asset["object_key"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    return AssetDownloadResponse(url=url, expires_in_seconds=settings.s3_presign_seconds)


@router.post("/assets/{asset_id}/catalogize", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def catalogize_asset(asset_id: str, db: DB, user: CurrentUser, _: Settings = Depends(require_ai_configuration)) -> JobCreatedResponse:
    require_ai_consent(db, user)
    get_owned_asset_or_404(db, user["id"], asset_id)
    job = job_document(user_id=user["id"], job_type="catalogue", input_ref={"asset_id": asset_id})
    job["model_provider"] = "local_ml+openai"
    db.ai_jobs.insert_one(job)
    return JobCreatedResponse(job_id=job["id"])


@router.get("/garments", response_model=list[GarmentResponse])
def list_garments(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    return list(db.garments.find({"user_id": user["id"]}).sort("created_at", DESCENDING))


@router.patch("/garments/{garment_id}", response_model=GarmentResponse)
def update_garment(garment_id: str, payload: GarmentPatch, db: DB, user: CurrentUser) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    updates["updated_at"] = utcnow()
    garment = db.garments.find_one_and_update(
        {"id": garment_id, "user_id": user["id"]},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found")
    return garment


@router.post("/wardrobe-searches", response_model=WardrobeSearchCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def create_wardrobe_search(
    payload: WardrobeSearchRequest,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_recommendation_configuration),
) -> WardrobeSearchCreatedResponse:
    require_ai_consent(db, user)
    search = document(user_id=user["id"], prompt=payload.prompt.strip(), status="queued", result=None, error_message=None)
    db.wardrobe_searches.insert_one(search)
    job = job_document(user_id=user["id"], job_type="recommendation", input_ref={"search_id": search["id"]})
    job["model_provider"] = "fpt_preferred+openai+qdrant"
    db.ai_jobs.insert_one(job)
    return WardrobeSearchCreatedResponse(search_id=search["id"], job_id=job["id"])


@router.get("/wardrobe-searches/{search_id}", response_model=WardrobeSearchResponse)
def get_wardrobe_search(search_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    search = db.wardrobe_searches.find_one({"id": search_id, "user_id": user["id"]})
    if not search:
        raise HTTPException(status_code=404, detail="Wardrobe search not found")
    return search


@router.post("/lookbooks", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def create_lookbook(
    payload: LookbookCreateRequest,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    garment_ids = list(dict.fromkeys(payload.garment_ids))
    garment_count = db.garments.count_documents({"user_id": user["id"], "id": {"$in": garment_ids}})
    if garment_count != len(garment_ids):
        raise HTTPException(status_code=422, detail="Every selected garment must belong to the current user")
    for optional_asset in (payload.pose_asset_id, payload.background_asset_id):
        if optional_asset:
            get_owned_asset_or_404(db, user["id"], optional_asset)
    lookbook = document(
        user_id=user["id"],
        title=payload.title,
        theme=payload.theme,
        occasion=payload.occasion,
        status="generating",
        cover_asset_id=None,
    )
    db.lookbooks.insert_one(lookbook)
    job = job_document(
        user_id=user["id"],
        job_type="lookbook",
        input_ref={
            "lookbook_id": lookbook["id"],
            "garment_ids": garment_ids,
            "pose_asset_id": payload.pose_asset_id,
            "background_asset_id": payload.background_asset_id,
            "instruction": payload.instruction,
        },
    )
    db.ai_jobs.insert_one(job)
    return JobCreatedResponse(job_id=job["id"])


@router.get("/lookbooks", response_model=list[LookbookResponse])
def list_lookbooks(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    return list(db.lookbooks.find({"user_id": user["id"]}).sort("created_at", DESCENDING))


@router.get("/lookbooks/{lookbook_id}/looks", response_model=list[LookResponse])
def list_looks(lookbook_id: str, db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    get_owned_lookbook_or_404(db, user["id"], lookbook_id)
    return list(db.looks.find({"lookbook_id": lookbook_id}).sort("created_at", DESCENDING))


@router.post("/looks/{look_id}/variations", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def create_variation(
    look_id: str,
    payload: VariationRequest,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    look = db.looks.find_one({"id": look_id})
    if not look or not db.lookbooks.find_one({"id": look["lookbook_id"], "user_id": user["id"]}):
        raise HTTPException(status_code=404, detail="Look not found")
    garment_ids = [item["garment_id"] for item in db.look_items.find({"look_id": look_id})]
    job = job_document(
        user_id=user["id"],
        job_type="variation",
        input_ref={"lookbook_id": look["lookbook_id"], "garment_ids": garment_ids, "instruction": payload.instruction},
    )
    db.ai_jobs.insert_one(job)
    return JobCreatedResponse(job_id=job["id"])


@router.post("/looks/{look_id}/renders", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def render_existing_look(
    look_id: str,
    db: DB,
    user: CurrentUser,
    _: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    """Render a fresh synthetic-model image from an owned, previously generated outfit selection."""
    require_ai_consent(db, user)
    look = db.looks.find_one({"id": look_id})
    if not look or not db.lookbooks.find_one({"id": look["lookbook_id"], "user_id": user["id"]}):
        raise HTTPException(status_code=404, detail="Look not found")
    garment_ids = [item["garment_id"] for item in db.look_items.find({"look_id": look_id})]
    if not garment_ids:
        raise HTTPException(status_code=409, detail="Look has no garments to render")
    job = job_document(
        user_id=user["id"], job_type="render",
        input_ref={"lookbook_id": look["lookbook_id"], "garment_ids": garment_ids, "instruction": "Create a new synthetic-model render."},
    )
    job["model_provider"] = "openai"
    db.ai_jobs.insert_one(job)
    return JobCreatedResponse(job_id=job["id"])


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    job = db.ai_jobs.find_one({"id": job_id, "user_id": user["id"]})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: str, db: DB, user: CurrentUser, _: Settings = Depends(require_ai_configuration)) -> JobCreatedResponse:
    require_ai_consent(db, user)
    job = db.ai_jobs.find_one({"id": job_id, "user_id": user["id"]})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "failed":
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried")
    db.ai_jobs.update_one(
        {"_id": job["_id"]},
        {"$set": {"status": "queued", "error_code": None, "error_message": None, "error_retryable": False, "started_at": None, "completed_at": None, "updated_at": utcnow()}},
    )
    return JobCreatedResponse(job_id=job["id"])


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(db: DB, user: CurrentUser, approval_status: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"user_id": user["id"]}
    if approval_status:
        query["status"] = approval_status
    return list(db.approval_requests.find(query).sort("requested_at", DESCENDING))


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalResponse)
def decide_approval(approval_id: str, payload: ApprovalDecisionRequest, db: DB, user: CurrentUser) -> dict[str, Any]:
    approval = db.approval_requests.find_one({"id": approval_id, "user_id": user["id"]})
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if approval["status"] != "pending":
        raise HTTPException(status_code=409, detail="Approval request has already been decided")
    now = utcnow()
    updates = {
        "status": payload.decision,
        "decided_at": now,
        "decision_payload": {"note": payload.note} if payload.note else {},
        "updated_at": now,
    }
    if approval["subject_type"] == "look":
        look = db.looks.find_one({"id": approval["subject_id"]})
        if not look:
            raise HTTPException(status_code=409, detail="Approval subject no longer exists")
        get_owned_lookbook_or_404(db, user["id"], look["lookbook_id"])
        db.looks.update_one({"_id": look["_id"]}, {"$set": {"status": "accepted" if payload.decision == "accepted" else "rejected", "updated_at": now}})
        db.lookbooks.update_one(
            {"id": look["lookbook_id"]},
            {"$set": {"status": "approved" if payload.decision == "accepted" else "draft", "updated_at": now}},
        )
    db.approval_requests.update_one({"_id": approval["_id"]}, {"$set": updates})
    return {**approval, **updates}
