from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import GarmentAttributes
from garblo.asset_cleanup import TEMPORARY_INPUT
from garblo.catalogue.metrics import summarize_catalogue
from garblo.config import Settings, get_settings
from garblo.consent import active_ai_consent, latest_consent
from garblo.database import get_db
from garblo.dependencies import get_current_user, require_ai_configuration
from garblo.images import sanitize_image
from garblo.jobs import attempt_limit, ensure_job_configuration, new_snapshot
from garblo.models import (
    AiCallAttempt,
    AiJob,
    ApprovalRequest,
    AssetCleanup,
    ConsentRecord,
    Garment,
    GarmentCandidate,
    Look,
    Lookbook,
    LookItem,
    MediaAsset,
    StyleSignal,
    UploadOperation,
    User,
    new_id,
)
from garblo.presets import PRESETS, select_preset
from garblo.recommendations.models import RecommendationSession, RecommendedItem, RecommendedOutfit
from garblo.recommendations.service import lock_user
from garblo.schemas import (
    AICallResponse,
    ApprovalDecisionRequest,
    ApprovalResponse,
    AssetDownloadResponse,
    AssetResponse,
    CandidatePatch,
    CandidateResponse,
    CatalogueRequest,
    CommitCandidatesRequest,
    CommitCandidatesResponse,
    ConsentRequest,
    ConsentResponse,
    GarmentPatch,
    GarmentResponse,
    JobCreatedResponse,
    JobResponse,
    LoginRequest,
    LookbookCreateRequest,
    LookbookResponse,
    LookFeedbackRequest,
    LookFeedbackResponse,
    LookResponse,
    RegisterRequest,
    TokenResponse,
    UploadOperationResponse,
    UserResponse,
    VariationRequest,
)
from garblo.security import create_access_token, hash_password, verify_password
from garblo.storage import ObjectStorage, StorageUnavailable, storage_key
from garblo.uploads import upload_with_key

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


def provider_http_error(exc: AIProviderError) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)})


def require_ai_consent(db: Session, user: User) -> None:
    if not active_ai_consent(db, user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active ai_processing consent is required before starting an AI job",
        )


def get_owned_asset_or_404(db: Session, user_id: str, asset_id: str) -> MediaAsset:
    asset = db.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.user_id == user_id)
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


def get_owned_lookbook_or_404(db: Session, user_id: str, lookbook_id: str) -> Lookbook:
    lookbook = db.scalar(
        select(Lookbook).where(Lookbook.id == lookbook_id, Lookbook.user_id == user_id)
    )
    if not lookbook:
        raise HTTPException(status_code=404, detail="Lookbook not found")
    return lookbook


@router.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DB) -> User:
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, db: DB, settings: Settings = Depends(get_settings)
) -> TokenResponse:
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
    consent = ConsentRecord(
        user_id=user.id, purpose=payload.purpose, policy_version=payload.policy_version
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent


@router.get("/me/ai-metrics")
def ai_metrics(db: DB, user: CurrentUser):
    return summarize_catalogue(db, user.id)


@router.get("/me/consents/latest", response_model=ConsentResponse | None)
def get_latest_consent(
    db: DB,
    user: CurrentUser,
    purpose: Annotated[str, Query(min_length=2, max_length=80)] = "ai_processing",
) -> ConsentRecord | None:
    return latest_consent(db, user.id, purpose)


@router.delete("/consents/{consent_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_consent(consent_id: str, db: DB, user: CurrentUser) -> Response:
    consent = db.scalar(
        select(ConsentRecord).where(
            ConsentRecord.id == consent_id, ConsentRecord.user_id == user.id
        )
    )
    if not consent:
        raise HTTPException(status_code=404, detail="Consent record not found")
    if not consent.revoked_at:
        consent.revoked_at = utcnow()
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assets", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
def upload_asset(
    db: DB,
    user: CurrentUser,
    file: UploadFile = File(...),
    asset_type: str = Form("garment_photo"),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    ] = None,
) -> MediaAsset:
    # Decode ảnh, S3 và SQLAlchemy đều sync: để FastAPI chạy route trong threadpool.
    body = file.file.read(settings.max_upload_bytes + 1)
    if not body:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(body) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the size limit")
    try:
        body, mime_type = sanitize_image(body, settings.max_upload_bytes, settings.max_image_pixels)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    checksum = hashlib.sha256(body).hexdigest()
    if idempotency_key is not None:
        return upload_with_key(
            db, user.id, idempotency_key, body, mime_type, asset_type, storage_or_503(settings)
        )
    if settings.catalogue_deduplicate_uploads:
        # Cùng user dùng cùng khóa để serialize cả trường hợp chưa có checksum nào.
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        existing = db.scalar(
            select(MediaAsset)
            .where(
                MediaAsset.user_id == user.id,
                MediaAsset.checksum == checksum,
                MediaAsset.asset_type == asset_type[:40],
                MediaAsset.status != "deleted",
            )
            .order_by(MediaAsset.created_at)
            .limit(1)
        )
        if existing:
            return existing
    asset_id = new_id()
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime_type]
    object_key = storage_key(user.id, asset_id, f"upload.{extension}")
    asset = MediaAsset(
        id=asset_id,
        user_id=user.id,
        object_key=object_key,
        asset_type=asset_type[:40],
        mime_type=mime_type,
        checksum=checksum,
        byte_size=len(body),
        status="uploaded",
        metadata_json={"retention_policy": TEMPORARY_INPUT}
        if asset_type == "garment_photo"
        else {},
    )
    storage = storage_or_503(settings)
    try:
        storage.put_bytes(object_key, body, mime_type)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


@router.get("/assets", response_model=list[AssetResponse])
def list_assets(db: DB, user: CurrentUser) -> list[MediaAsset]:
    return list(
        db.scalars(
            select(MediaAsset)
            .where(MediaAsset.user_id == user.id)
            .order_by(MediaAsset.created_at.desc())
        )
    )


@router.get("/assets/by-request/{request_key}", response_model=UploadOperationResponse)
def get_upload_operation(request_key: str, db: DB, user: CurrentUser) -> UploadOperation:
    operation = db.scalar(
        select(UploadOperation).where(
            UploadOperation.user_id == user.id, UploadOperation.request_key == request_key
        )
    )
    if not operation:
        raise HTTPException(status_code=404, detail="Không tìm thấy upload operation")
    return operation


@router.get("/assets/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: str, db: DB, user: CurrentUser) -> MediaAsset:
    return get_owned_asset_or_404(db, user.id, asset_id)


@router.get("/assets/{asset_id}/jobs", response_model=list[JobResponse])
def get_asset_jobs(asset_id: str, db: DB, user: CurrentUser) -> list[JobResponse]:
    get_owned_asset_or_404(db, user.id, asset_id)
    jobs = list(
        db.scalars(
            select(AiJob).where(
                AiJob.user_id == user.id,
                AiJob.job_type == "catalogue",
                AiJob.input_ref["asset_id"].as_string() == asset_id,
            )
        )
    )
    if len(jobs) > 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "ambiguous_catalogue_jobs", "message": "Nguồn có nhiều job catalogue"},
        )
    return [
        JobResponse.model_validate(job).model_copy(update={"model": job.input_ref.get("model")})
        for job in jobs
    ]


@router.get("/assets/{asset_id}/download", response_model=AssetDownloadResponse)
def get_asset_download(
    asset_id: str, db: DB, user: CurrentUser, settings: Settings = Depends(get_settings)
) -> AssetDownloadResponse:
    asset = get_owned_asset_or_404(db, user.id, asset_id)
    if asset.status == "deleted" or db.get(AssetCleanup, asset.id):
        raise HTTPException(
            status_code=410,
            detail={"code": "asset_deleted", "message": "Ảnh này đã bị xóa khỏi bộ nhớ."},
        )
    try:
        url = storage_or_503(settings).presign_get(asset.object_key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    return AssetDownloadResponse(url=url, expires_in_seconds=settings.s3_presign_seconds)


@router.post(
    "/assets/{asset_id}/catalogize",
    response_model=JobCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def catalogize_asset(
    asset_id: str,
    db: DB,
    user: CurrentUser,
    payload: CatalogueRequest | None = None,
    settings: Settings = Depends(get_settings),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    asset = db.scalar(
        select(MediaAsset)
        .where(MediaAsset.id == asset_id, MediaAsset.user_id == user.id)
        .with_for_update()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    provider = (payload or CatalogueRequest()).vlm_provider
    try:
        snapshot = new_snapshot(settings, provider, "catalogue")
    except AIProviderError as exc:
        raise provider_http_error(exc) from exc
    if asset.asset_type != "garment_photo":
        raise HTTPException(status_code=422, detail="Chỉ phân tích garment_photo")
    if asset.status != "uploaded":
        raise HTTPException(
            status_code=409,
            detail={"code": "analysis_already_exists", "message": "Dùng job/candidates hiện có"},
        )
    asset.status = "analyzing"
    job = AiJob(
        user_id=user.id,
        job_type="catalogue",
        model_provider=provider,
        input_ref={
            "asset_id": asset_id,
            **snapshot,
            "retention_policy": asset.metadata_json.get("retention_policy", "permanent"),
        },
    )
    db.add(job)
    db.commit()
    return JobCreatedResponse(job_id=job.id)


@router.get("/assets/{asset_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(asset_id: str, db: DB, user: CurrentUser):
    get_owned_asset_or_404(db, user.id, asset_id)
    return list(
        db.scalars(
            select(GarmentCandidate)
            .where(GarmentCandidate.asset_id == asset_id, GarmentCandidate.user_id == user.id)
            .order_by(GarmentCandidate.created_at, GarmentCandidate.candidate_index)
        )
    )


@router.patch("/candidates/{candidate_id}", response_model=CandidateResponse)
def patch_candidate(candidate_id: str, payload: CandidatePatch, db: DB, user: CurrentUser):
    source_id = db.scalar(
        select(GarmentCandidate.asset_id).where(
            GarmentCandidate.id == candidate_id, GarmentCandidate.user_id == user.id
        )
    )
    if not source_id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    asset = db.scalar(select(MediaAsset).where(MediaAsset.id == source_id).with_for_update())
    candidate = db.scalar(
        select(GarmentCandidate)
        .where(GarmentCandidate.id == candidate_id, GarmentCandidate.user_id == user.id)
        .with_for_update()
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if candidate.status == "committed":
        raise HTTPException(status_code=409, detail="Candidate đã commit; sửa garment thay thế")
    changes = payload.model_dump(exclude_unset=True)
    details = changes.pop("details", None)
    new_status = changes.pop("status", None)
    if "status" in payload.model_fields_set and new_status is None:
        raise HTTPException(status_code=422, detail="status không được null")
    candidate.attributes_json = {
        **candidate.attributes_json,
        **changes,
    }
    if details is not None:
        candidate.details_json = {**candidate.details_json, **details}
    if new_status:
        candidate.status = new_status
    candidate.review_json = {
        **candidate.review_json,
        "edited_by_user": True,
        "edited_fields": sorted(set(candidate.review_json.get("edited_fields", [])) | set(changes)),
    }
    sync_review_asset(db, asset)
    db.commit()
    db.refresh(candidate)
    return candidate


def sync_review_asset(db: Session, asset: MediaAsset) -> None:
    db.flush()
    states = list(
        db.scalars(select(GarmentCandidate.status).where(GarmentCandidate.asset_id == asset.id))
    )
    if "draft" in states:
        asset.status = "partially_catalogued" if "committed" in states else "analyzed"
    elif "committed" in states:
        asset.status = "catalogued"
    elif states:
        asset.status = "rejected"


@router.post("/assets/{asset_id}/commit", response_model=CommitCandidatesResponse)
def commit_candidates(asset_id: str, payload: CommitCandidatesRequest, db: DB, user: CurrentUser):
    asset = db.scalar(
        select(MediaAsset)
        .where(MediaAsset.id == asset_id, MediaAsset.user_id == user.id)
        .with_for_update()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    ids = list(dict.fromkeys(payload.candidate_ids))
    candidates = list(
        db.scalars(
            select(GarmentCandidate)
            .where(
                GarmentCandidate.id.in_(ids),
                GarmentCandidate.asset_id == asset_id,
                GarmentCandidate.user_id == user.id,
            )
            .order_by(GarmentCandidate.id)
            .with_for_update()
        )
    )
    if len(candidates) != len(ids):
        raise HTTPException(status_code=404, detail="Candidate not found for this asset")
    # Validate tất cả trước khi tạo bất kỳ garment nào; không spread AI JSON vào ORM.
    for candidate in candidates:
        if candidate.status == "committed" and candidate.garment_id:
            continue
        if candidate.status != "draft":
            raise HTTPException(status_code=409, detail="Candidate không thể commit")
        attributes = GarmentAttributes.model_validate(candidate.attributes_json)
        if not attributes.category or attributes.category.strip().lower() in {
            "",
            "unknown",
            "unclassified",
        }:
            raise HTTPException(status_code=422, detail="Xác nhận danh mục trước khi lưu vào tủ")
    for candidate in candidates:
        if candidate.status == "committed" and candidate.garment_id:
            continue
        if candidate.status != "draft":
            raise HTTPException(status_code=409, detail="Candidate không thể commit")
        attributes = GarmentAttributes.model_validate(candidate.attributes_json)
        garment = Garment(
            user_id=user.id,
            source_asset_id=asset_id,
            category=attributes.category,
            color=attributes.color,
            material=attributes.material,
            season=attributes.season,
            tags=attributes.tags,
            details_json=dict(candidate.details_json),
            component_asset_id=candidate.component_asset_id,
        )
        db.add(garment)
        db.flush()
        candidate.garment_id, candidate.status = garment.id, "committed"
    sync_review_asset(db, asset)
    by_id = {candidate.id: candidate.garment_id for candidate in candidates}
    db.commit()
    return CommitCandidatesResponse(garment_ids=[by_id[candidate_id] for candidate_id in ids])


@router.get("/garments", response_model=list[GarmentResponse])
def list_garments(
    db: DB,
    user: CurrentUser,
    category: str | None = None,
    tags: Annotated[list[str] | None, Query()] = None,
) -> list[Garment]:
    statement = select(Garment).where(Garment.user_id == user.id)
    if category:
        statement = statement.where(Garment.category == category)
    for index, tag in enumerate(tags or []):
        if db.get_bind().dialect.name == "postgresql":
            statement = statement.where(cast(Garment.tags, JSONB).contains([tag]))
        else:
            values = func.json_each(Garment.tags).table_valued("value").alias(f"tag_{index}")
            statement = statement.where(
                select(1).select_from(values).where(values.c.value == tag).exists()
            )
    return list(db.scalars(statement.order_by(Garment.created_at.desc())))


@router.get("/garments/{garment_id}", response_model=GarmentResponse)
def get_garment(garment_id: str, db: DB, user: CurrentUser):
    garment = db.scalar(select(Garment).where(Garment.id == garment_id, Garment.user_id == user.id))
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found")
    return garment


@router.patch("/garments/{garment_id}", response_model=GarmentResponse)
def update_garment(garment_id: str, payload: GarmentPatch, db: DB, user: CurrentUser) -> Garment:
    garment = db.scalar(
        select(Garment)
        .where(Garment.id == garment_id, Garment.user_id == user.id)
        .with_for_update()
    )
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "details":
            if value is not None:
                garment.details_json = {**garment.details_json, **value}
        else:
            setattr(garment, field, value)
    db.commit()
    db.refresh(garment)
    return garment


@router.delete("/garments/{garment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_garment(
    garment_id: str,
    db: DB,
    user: CurrentUser,
    delete_recommended_outfits: bool = Query(default=False),
    settings: Settings = Depends(get_settings),
) -> Response:
    lock_user(db, user.id)
    # Cùng thứ tự với recommendation: user -> session -> outfit -> garment.
    related_outfits = select(RecommendedItem.outfit_id).where(
        RecommendedItem.garment_id == garment_id
    )
    session_ids = list(
        db.scalars(
            select(RecommendationSession.id)
            .where(
                RecommendationSession.user_id == user.id,
                RecommendationSession.id.in_(
                    select(RecommendedOutfit.session_id).where(
                        RecommendedOutfit.id.in_(related_outfits)
                    )
                ),
            )
            .order_by(RecommendationSession.id)
            .with_for_update()
        )
    )
    outfit_ids = list(
        db.scalars(
            select(RecommendedOutfit.id)
            .where(
                RecommendedOutfit.session_id.in_(session_ids),
                RecommendedOutfit.id.in_(related_outfits),
            )
            .order_by(RecommendedOutfit.id)
            .with_for_update()
        )
    )
    garment = db.scalar(
        select(Garment)
        .where(Garment.id == garment_id, Garment.user_id == user.id)
        .with_for_update()
    )
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found")

    if outfit_ids and not delete_recommended_outfits:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "recommendation_delete_confirmation_required",
                "message": "Cần xác nhận xóa các bộ phối gợi ý chứa món này cùng phản hồi của bạn.",
            },
        )

    if db.scalar(select(LookItem.id).where(LookItem.garment_id == garment.id).limit(1)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "garment_in_look",
                "message": "Món đồ đã được lưu trong bộ phối nên không thể xóa.",
            },
        )

    asset_ids = list(
        dict.fromkeys(filter(None, [garment.source_asset_id, garment.component_asset_id]))
    )
    # Ảnh gốc dùng chung chỉ là lineage; ảnh component mới là ảnh của món.
    source = db.get(MediaAsset, garment.source_asset_id)
    other_source = db.scalar(
        select(Garment.id)
        .where(
            Garment.id != garment.id,
            Garment.source_asset_id == garment.source_asset_id,
        )
        .limit(1)
    )
    pending_source = db.scalar(
        select(GarmentCandidate.id)
        .where(
            GarmentCandidate.asset_id == garment.source_asset_id,
            GarmentCandidate.status.in_(["draft", "rejected"]),
        )
        .limit(1)
    )
    if (
        other_source
        or pending_source
        or source.metadata_json.get("retention_policy") == TEMPORARY_INPUT
    ):
        asset_ids = [aid for aid in asset_ids if aid != garment.source_asset_id]
    shared = db.scalar(
        select(Garment.id)
        .where(
            Garment.id != garment.id,
            or_(Garment.source_asset_id.in_(asset_ids), Garment.component_asset_id.in_(asset_ids)),
        )
        .limit(1)
    )
    if shared:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "garment_asset_shared",
                "message": "Ảnh của món đồ đang được món khác sử dụng nên không thể xóa.",
            },
        )

    assets = list(db.scalars(select(MediaAsset).where(MediaAsset.id.in_(asset_ids))))
    try:
        candidates = db.scalars(
            select(GarmentCandidate).where(GarmentCandidate.garment_id == garment.id)
        )
        for candidate in candidates:
            candidate.details_json = {
                **candidate.details_json,
                "deleted_garment_id": garment.id,
            }
            candidate.garment_id = None
            candidate.status = "deleted"
        # DB cascade xóa cả items và feedback; giữ lại phiên và audit job.
        db.execute(delete(RecommendedOutfit).where(RecommendedOutfit.id.in_(outfit_ids)))
        db.delete(garment)
        # Phát hiện liên kết DB bị chặn trước khi xóa bất kỳ object ảnh nào.
        db.flush()
        try:
            storage = storage_or_503(settings)
            for asset in assets:
                if asset.status != "deleted":
                    storage.delete(asset.object_key)
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "storage_delete_failed",
                    "message": "Chưa xóa được ảnh. Món đồ vẫn còn trong tủ, bạn có thể thử lại.",
                },
            ) from exc
        for asset in assets:
            asset.status = "deleted"
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "garment_delete_conflict",
                "message": "Món đồ còn được dữ liệu khác sử dụng nên chưa thể xóa. Hãy tải lại và thử lại.",
            },
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/lookbooks", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
def create_lookbook(
    payload: LookbookCreateRequest,
    db: DB,
    user: CurrentUser,
    settings: Settings = Depends(require_ai_configuration),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    try:
        preset_id = select_preset(payload)
        snapshot = new_snapshot(settings, "openai", "lookbook")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AIProviderError as exc:
        raise provider_http_error(exc) from exc
    garment_ids = list(dict.fromkeys(payload.garment_ids))
    garments = list(
        db.scalars(select(Garment).where(Garment.user_id == user.id, Garment.id.in_(garment_ids)))
    )
    if len(garments) != len(garment_ids):
        raise HTTPException(
            status_code=422, detail="Every selected garment must belong to the current user"
        )
    for optional_asset in (payload.pose_asset_id, payload.background_asset_id):
        if optional_asset:
            get_owned_asset_or_404(db, user.id, optional_asset)
    lookbook = Lookbook(
        user_id=user.id,
        title=payload.title,
        theme=preset_id,
        occasion=preset_id,
        status="generating",
    )
    db.add(lookbook)
    db.flush()
    job = AiJob(
        user_id=user.id,
        job_type="lookbook",
        model_provider="openai",
        input_ref={
            **snapshot,
            "lookbook_id": lookbook.id,
            "garment_ids": garment_ids,
            "pose_asset_id": payload.pose_asset_id,
            "background_asset_id": payload.background_asset_id,
            "preset_id": preset_id,
            "instruction": PRESETS[preset_id],
        },
    )
    db.add(job)
    db.commit()
    return JobCreatedResponse(job_id=job.id)


@router.get("/lookbooks", response_model=list[LookbookResponse])
def list_lookbooks(db: DB, user: CurrentUser) -> list[Lookbook]:
    return list(
        db.scalars(
            select(Lookbook).where(Lookbook.user_id == user.id).order_by(Lookbook.created_at.desc())
        )
    )


@router.get("/lookbooks/{lookbook_id}/looks", response_model=list[LookResponse])
def list_looks(lookbook_id: str, db: DB, user: CurrentUser) -> list[LookResponse]:
    get_owned_lookbook_or_404(db, user.id, lookbook_id)
    looks = list(
        db.scalars(
            select(Look).where(Look.lookbook_id == lookbook_id).order_by(Look.created_at.desc())
        )
    )
    ids = [look.id for look in looks]
    items = list(
        db.scalars(select(LookItem).where(LookItem.look_id.in_(ids)).order_by(LookItem.layer_order))
    )
    approvals = list(
        db.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.subject_type == "look",
                ApprovalRequest.subject_id.in_(ids),
                ApprovalRequest.user_id == user.id,
            )
        )
    )
    return [
        LookResponse.model_validate(look).model_copy(
            update={
                "garment_ids": [item.garment_id for item in items if item.look_id == look.id],
                "approval_id": next((a.id for a in approvals if a.subject_id == look.id), None),
            }
        )
        for look in looks
    ]


@router.post(
    "/looks/{look_id}/variations",
    response_model=JobCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
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
    # Route được giữ để client nhận lỗi nghiệp vụ rõ thay vì âm thầm bỏ instruction.
    raise HTTPException(status_code=422, detail="P0 tạm khóa chỉnh look bằng prompt tự do")


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: DB, user: CurrentUser) -> JobResponse:
    job = db.scalar(select(AiJob).where(AiJob.id == job_id, AiJob.user_id == user.id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.model_validate(job).model_copy(update={"model": job.input_ref.get("model")})


@router.post(
    "/jobs/{job_id}/retry", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED
)
def retry_job(
    job_id: str,
    db: DB,
    user: CurrentUser,
    settings: Settings = Depends(get_settings),
) -> JobCreatedResponse:
    require_ai_consent(db, user)
    job = db.scalar(
        select(AiJob).where(AiJob.id == job_id, AiJob.user_id == user.id).with_for_update()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried")
    if job.attempts >= attempt_limit(settings, job.input_ref):
        raise HTTPException(
            status_code=409,
            detail={"code": "retry_limit_exceeded", "message": "Đã hết số lần xử lý cho job"},
        )
    try:
        ensure_job_configuration(settings, job.model_provider, job.input_ref, job.job_type)
    except AIProviderError as exc:
        raise provider_http_error(exc) from exc
    job.status = "queued"
    job.error_code = None
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.next_attempt_at = None
    job.lock_token = None
    job.locked_at = None
    if job.job_type == "catalogue":
        asset = get_owned_asset_or_404(db, user.id, str(job.input_ref.get("asset_id", "")))
        asset.status = "analyzing"
    elif job.job_type in {"lookbook", "variation"}:
        lookbook = get_owned_lookbook_or_404(db, user.id, str(job.input_ref.get("lookbook_id", "")))
        lookbook.status = "generating"
    db.commit()
    return JobCreatedResponse(job_id=job.id)


@router.get("/jobs/{job_id}/calls", response_model=list[AICallResponse])
def job_calls(job_id: str, db: DB, user: CurrentUser):
    if not db.scalar(select(AiJob.id).where(AiJob.id == job_id, AiJob.user_id == user.id)):
        raise HTTPException(status_code=404, detail="Job not found")
    return list(
        db.scalars(
            select(AiCallAttempt)
            .where(AiCallAttempt.job_id == job_id, AiCallAttempt.user_id == user.id)
            .order_by(AiCallAttempt.call_index)
        )
    )


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(
    db: DB, user: CurrentUser, approval_status: str | None = None
) -> list[ApprovalRequest]:
    statement = select(ApprovalRequest).where(ApprovalRequest.user_id == user.id)
    if approval_status:
        statement = statement.where(ApprovalRequest.status == approval_status)
    return list(db.scalars(statement.order_by(ApprovalRequest.requested_at.desc())))


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalResponse)
def decide_approval(
    approval_id: str, payload: ApprovalDecisionRequest, db: DB, user: CurrentUser
) -> ApprovalRequest:
    approval = db.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user.id)
        .with_for_update()
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


@router.post("/looks/{look_id}/feedback", response_model=LookFeedbackResponse, status_code=201)
def look_feedback(look_id: str, payload: LookFeedbackRequest, db: DB, user: CurrentUser):
    look = db.scalar(
        select(Look).join(Lookbook).where(Look.id == look_id, Lookbook.user_id == user.id)
    )
    if not look:
        raise HTTPException(status_code=404, detail="Look not found")
    signal = StyleSignal(
        user_id=user.id,
        look_id=look.id,
        signal=payload.signal,
        weight=1 if payload.signal == "like" else -1,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal
