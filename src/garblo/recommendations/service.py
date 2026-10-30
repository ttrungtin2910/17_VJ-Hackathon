from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from garblo.models import Garment, User
from garblo.recommendations import engine, schemas
from garblo.recommendations.models import (
    RecommendationFeedback,
    RecommendationSession,
    RecommendedItem,
    RecommendedOutfit,
)


def fail(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def lock_user(db: Session, user_id: str) -> None:
    # Thứ tự khóa chung: user -> session -> outfit -> garments (ID tăng dần).
    if not db.scalar(select(User.id).where(User.id == user_id).with_for_update()):
        fail(404, "user_not_found", "Không tìm thấy người dùng.")


def get_owned_session(
    db: Session, user_id: str, session_id: str, *, lock: bool = False
) -> RecommendationSession:
    statement = select(RecommendationSession).where(
        RecommendationSession.id == session_id, RecommendationSession.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    result = db.scalar(statement)
    if result is None:
        fail(404, "session_not_found", "Không tìm thấy phiên phối đồ.")
    return result


def get_owned_outfit(
    db: Session, user_id: str, session_id: str, outfit_id: str, *, lock: bool = False
) -> RecommendedOutfit:
    get_owned_session(db, user_id, session_id, lock=lock)
    statement = select(RecommendedOutfit).where(
        RecommendedOutfit.id == outfit_id, RecommendedOutfit.session_id == session_id
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    result = db.scalar(statement)
    if result is None:
        fail(404, "outfit_not_found", "Không tìm thấy bộ đồ trong phiên này.")
    return result


def garment_snapshot(garment: Garment) -> schemas.GarmentSnapshot | None:
    role = engine.category_role(garment.category)
    if role is None or "category" in garment.details_json.get("uncertain_fields", []):
        return None
    return schemas.GarmentSnapshot(
        garment_id=garment.id,
        role=role,
        category=garment.category,
        color=garment.color,
        material=garment.material,
        season=garment.season,
        tags=garment.tags,
        details=deepcopy(garment.details_json),
        wear_count=garment.wear_count,
        last_worn_at=garment.last_worn_at.isoformat() if garment.last_worn_at else None,
        source_asset_id=garment.source_asset_id,
        component_asset_id=garment.component_asset_id,
    )


def owned_wardrobe(
    db: Session, user_id: str, *, lock: bool = False
) -> list[schemas.GarmentSnapshot]:
    statement = (
        select(Garment)
        .where(Garment.user_id == user_id, Garment.catalog_status == "active")
        .order_by(Garment.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    return [
        snapshot
        for garment in db.scalars(statement)
        if (snapshot := garment_snapshot(garment)) is not None
    ]


def personalization_weights(db: Session, user_id: str) -> dict[str, float]:
    rows = db.scalars(
        select(RecommendationFeedback)
        .where(RecommendationFeedback.user_id == user_id)
        .order_by(RecommendationFeedback.created_at.desc(), RecommendationFeedback.id)
        .limit(1000)
    )
    # Không diễn giải StyleSignal/Look như dữ liệu đã học khi chưa có adapter.
    return engine.learn_weights([(row.signal, row.features_snapshot) for row in rows])


def outfit_response(db: Session, user_id: str, outfit: RecommendedOutfit) -> schemas.OutfitResponse:
    items = list(db.scalars(select(RecommendedItem).where(RecommendedItem.outfit_id == outfit.id)))
    snapshots = [schemas.GarmentSnapshot.model_validate(item.snapshot) for item in items]
    snapshots.sort(key=lambda item: (engine.ROLE_ORDER[item.role], item.garment_id))
    signal = db.scalar(
        select(RecommendationFeedback.signal).where(
            RecommendationFeedback.user_id == user_id,
            RecommendationFeedback.outfit_id == outfit.id,
        )
    )
    return schemas.OutfitResponse(
        id=outfit.id,
        session_id=outfit.session_id,
        rank=outfit.rank,
        revision=outfit.revision,
        items=snapshots,
        score=float(outfit.score),
        breakdown=schemas.ScoreBreakdown.model_validate(outfit.breakdown),
        explanation=outfit.explanation,
        features_snapshot=outfit.features_snapshot,
        feedback=signal,
    )


def session_response(
    db: Session, user_id: str, session: RecommendationSession
) -> schemas.SessionResponse:
    outfits = db.scalars(
        select(RecommendedOutfit)
        .where(RecommendedOutfit.session_id == session.id)
        .order_by(RecommendedOutfit.rank)
    )
    return schemas.SessionResponse(
        id=session.id,
        context=schemas.RecommendationContext.model_validate(session.context_json),
        location=session.location,
        engine_version=session.engine_version,
        weights_snapshot=session.weights_snapshot,
        outfits=[outfit_response(db, user_id, outfit) for outfit in outfits],
        reason=session.reason,
        missing_categories=session.missing_categories,
        score_notice=engine.SCORE_NOTICE,
        created_at=session.created_at,
    )


def create_session(
    db: Session, user_id: str, payload: schemas.CreateSessionRequest, idempotency_key: str
) -> schemas.SessionResponse:
    if not idempotency_key.strip() or len(idempotency_key) > 200:
        fail(422, "invalid_idempotency_key", "Idempotency-Key phải có từ 1 đến 200 ký tự.")
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical.encode()).hexdigest()
    lock_user(db, user_id)
    existing = db.scalar(
        select(RecommendationSession).where(
            RecommendationSession.user_id == user_id,
            RecommendationSession.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            fail(409, "idempotency_key_reused", "Khóa này đã dùng cho yêu cầu khác.")
        response = session_response(db, user_id, existing)
        db.commit()
        return response
    weights = personalization_weights(db, user_id)
    result = engine.recommend(owned_wardrobe(db, user_id, lock=True), payload.context, weights)
    session = RecommendationSession(
        user_id=user_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        context_json=payload.context.model_dump(mode="json"),
        location=payload.location,
        engine_version=engine.ENGINE_VERSION,
        weights_snapshot=weights,
        reason=result.reason,
        missing_categories=result.missing_categories,
    )
    db.add(session)
    db.flush()
    for rank, recommendation in enumerate(result.outfits, 1):
        outfit = RecommendedOutfit(
            session_id=session.id,
            rank=rank,
            revision=1,
            score=recommendation.score,
            breakdown=recommendation.breakdown.model_dump(),
            explanation=recommendation.explanation,
            features_snapshot=recommendation.features,
            creation_features=recommendation.features,
        )
        db.add(outfit)
        db.flush()
        for item in recommendation.items:
            db.add(
                RecommendedItem(
                    outfit_id=outfit.id,
                    garment_id=item.garment_id,
                    role=item.role,
                    snapshot=item.model_dump(mode="json"),
                )
            )
    db.flush()
    response = session_response(db, user_id, session)
    db.commit()
    return response


def list_sessions(
    db: Session, user_id: str, *, limit: int = 50, offset: int = 0
) -> list[schemas.SessionResponse]:
    sessions = db.scalars(
        select(RecommendationSession)
        .where(RecommendationSession.user_id == user_id)
        .order_by(RecommendationSession.created_at.desc(), RecommendationSession.id)
        .limit(limit)
        .offset(offset)
    )
    return [session_response(db, user_id, session) for session in sessions]


def apply_edit(
    db: Session,
    user_id: str,
    session_id: str,
    outfit_id: str,
    payload: schemas.EditRequest,
    *,
    commit: bool = True,
) -> schemas.OutfitResponse:
    """Chỉ đổi một slot; commit=False dành cho transaction/fence của worker.

    Caller chịu trách nhiệm rollback nếu có lỗi, kể cả khi commit=False.
    Không gọi provider hoặc diễn giải ngôn ngữ tự nhiên tại đây.
    """
    lock_user(db, user_id)
    session = get_owned_session(db, user_id, session_id, lock=True)
    outfit = get_owned_outfit(db, user_id, session_id, outfit_id, lock=True)
    if outfit.revision != payload.expected_revision:
        fail(409, "revision_conflict", "Bộ đồ đã thay đổi; vui lòng tải lại trước khi sửa.")
    rows = list(db.scalars(select(RecommendedItem).where(RecommendedItem.outfit_id == outfit.id)))
    snapshots = [schemas.GarmentSnapshot.model_validate(row.snapshot) for row in rows]
    current_ids = {item.garment_id for item in snapshots}
    locked = set(payload.locked_garment_ids)
    excluded = set(payload.excluded_garment_ids)
    if payload.target_garment_id not in current_ids or not locked <= current_ids:
        fail(422, "invalid_edit_target", "Món cần sửa và món khóa phải thuộc bộ đồ hiện tại.")
    if payload.target_garment_id in locked or locked & excluded:
        fail(422, "locked_slot", "Không thể thay hoặc loại món đã khóa.")
    unchanged_ids = current_ids - {payload.target_garment_id}
    if unchanged_ids & excluded:
        fail(422, "excluded_preserved_item", "Thay một slot không thể loại món ở slot khác.")
    context = schemas.RecommendationContext.model_validate(session.context_json)
    wardrobe = owned_wardrobe(db, user_id, lock=True)
    by_id = {item.garment_id: item for item in wardrobe}
    if not current_ids <= by_id.keys():
        fail(409, "wardrobe_changed", "Có món không còn hoạt động trong tủ; không thể lưu bộ này.")
    if any(by_id[item.garment_id].role != item.role for item in snapshots):
        fail(409, "wardrobe_changed", "Danh mục món đồ đã thay đổi; vui lòng tạo bộ mới.")
    if not engine.complete([by_id[item.garment_id] for item in snapshots], context):
        fail(409, "wardrobe_changed", "Thuộc tính tủ đồ hiện tại không còn phù hợp bộ này.")
    target = next(item for item in snapshots if item.garment_id == payload.target_garment_id)
    candidates = [
        item
        for item in wardrobe
        if item.role == target.role
        and item.garment_id not in current_ids | excluded
        and engine.matches_constraints(item, payload.constraints)
        and engine.weather_allowed(item, context)
    ]
    if payload.replacement_garment_id is not None:
        candidates = [
            item for item in candidates if item.garment_id == payload.replacement_garment_id
        ]
    ranked = []
    for candidate in candidates:
        proposed = [candidate if old.garment_id == target.garment_id else old for old in snapshots]
        if engine.complete(proposed, context):
            ranked.append(engine.score_outfit(proposed, context, session.weights_snapshot))
    if not ranked:
        fail(422, "no_valid_replacement", "Không có món thay thế hợp lệ cho slot này trong tủ.")
    chosen = min(ranked, key=lambda value: (-value.score, value.stable_key))
    replacement = next(item for item in chosen.items if item.role == target.role)
    # CAS bảo vệ cả khi dialect không hỗ trợ FOR UPDATE; thất bại không chạm item.
    changed = db.execute(
        update(RecommendedOutfit)
        .where(
            RecommendedOutfit.id == outfit_id,
            RecommendedOutfit.revision == payload.expected_revision,
        )
        .values(
            revision=payload.expected_revision + 1,
            score=chosen.score,
            breakdown=chosen.breakdown.model_dump(),
            explanation=chosen.explanation,
            features_snapshot=chosen.features,
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        fail(409, "revision_conflict", "Bộ đồ đã thay đổi; vui lòng tải lại trước khi sửa.")
    row = next(row for row in rows if row.garment_id == target.garment_id)
    row.garment_id = replacement.garment_id
    row.snapshot = replacement.model_dump(mode="json")
    db.flush()
    db.refresh(outfit)
    response = outfit_response(db, user_id, outfit)
    if commit:
        db.commit()
    return response


def put_feedback(
    db: Session, user_id: str, session_id: str, outfit_id: str, payload: schemas.FeedbackRequest
) -> schemas.FeedbackResponse:
    lock_user(db, user_id)
    outfit = get_owned_outfit(db, user_id, session_id, outfit_id, lock=True)
    row = db.scalar(
        select(RecommendationFeedback).where(
            RecommendationFeedback.user_id == user_id, RecommendationFeedback.outfit_id == outfit_id
        )
    )
    if row is None:
        db.add(
            RecommendationFeedback(
                user_id=user_id,
                outfit_id=outfit_id,
                signal=payload.signal,
                features_snapshot=deepcopy(outfit.features_snapshot),
            )
        )
    elif row.signal != payload.signal:
        row.signal = payload.signal
        # Flip thay dấu trên snapshot cũ, không tạo mẫu mới hoặc tăng trọng số.
    db.commit()
    return schemas.FeedbackResponse(outfit_id=outfit_id, signal=payload.signal)
