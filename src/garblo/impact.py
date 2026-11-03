"""Lượt mặc, thành tựu bền vững và nhiệm vụ tuần của từng người dùng."""

from datetime import date, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.database import get_db
from garblo.dependencies import get_current_user
from garblo.impact_models import ImpactProgress, WearEvent
from garblo.models import Garment, User, now_utc
from garblo.recommendations.models import RecommendationSession, RecommendedItem, RecommendedOutfit
from garblo.recommendations.service import get_owned_outfit, get_owned_session, lock_user

BADGES = [
    ("first-item", "Khởi đầu", 1, "items"),
    ("collector", "Nhà sưu tập", 10, "items"),
    ("stylist", "Stylist tập sự", 5, "outfits"),
    ("first-wear", "Lần mặc đầu tiên", 1, "wear"),
    ("five-wears", "Thói quen đang lớn", 5, "wear"),
    ("twenty-wears", "Đồ quen, nhiều lần mặc", 20, "wear"),
]
TASKS = [
    ("days", "Mặc trong 3 ngày", 3),
    ("outfits", "Thử 2 bộ phối", 2),
    ("items", "Cho 5 món lên sóng", 5),
]


def zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Múi giờ không hợp lệ") from None


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def sync_progress(db: Session, user_id: str) -> list[str]:
    """Caller giữ user lock; gọi trước commit của các thay đổi nguồn."""
    db.flush()
    state = db.get(ImpactProgress, user_id)
    if state is None:
        state = ImpactProgress(user_id=user_id, data={})
        db.add(state)
    data = dict(state.data)
    outfit_ids = set(data.get("outfits", []))
    outfit_ids.update(
        db.scalars(
            select(RecommendedOutfit.id)
            .join(RecommendationSession)
            .where(RecommendationSession.user_id == user_id)
        )
    )
    items = list(
        db.scalars(
            select(Garment.id).where(Garment.user_id == user_id, Garment.catalog_status == "active")
        )
    )
    events = list(db.scalars(select(WearEvent).where(WearEvent.user_id == user_id)))
    counts = {"items": len(items), "outfits": len(outfit_ids), "wear": len(events)}
    unlocked = dict(data.get("badges", {}))
    new = []
    for key, _, threshold, kind in BADGES:
        if key not in unlocked and counts[kind] >= threshold:
            unlocked[key] = now_utc().isoformat()
            new.append(key)
    weeks = dict(data.get("weeks", {}))
    for start in {week_start(event.worn_on) for event in events}:
        selected = [event for event in events if week_start(event.worn_on) == start]
        progress = {
            "days": len({e.worn_on for e in selected}),
            "outfits": len({e.outfit_id for e in selected}),
            "items": len({i for e in selected for i in e.item_ids}),
        }
        completed = dict(weeks.get(start.isoformat(), {}))
        for key, _, target in TASKS:
            if progress[key] >= target and key not in completed:
                completed[key] = now_utc().isoformat()
                new.append(f"{start}:{key}")
        weeks[start.isoformat()] = completed
    state.data = {"outfits": sorted(outfit_ids), "badges": unlocked, "weeks": weeks}
    return new


def event_response(event: WearEvent) -> dict:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "outfit_id": event.outfit_id,
        "revision": event.revision,
        "item_ids": event.item_ids,
        "worn_on": event.worn_on.isoformat(),
        "timezone": event.timezone,
        "created_at": event.created_at.isoformat(),
    }


class WearInput(BaseModel):
    worn_on: date
    timezone: str = Field(max_length=100)
    expected_revision: int = Field(ge=1)


router = APIRouter(tags=["impact"])
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/recommendations/{session_id}/outfits/{outfit_id}/wear-events", status_code=201)
def record_wear(
    session_id: str,
    outfit_id: str,
    payload: WearInput,
    response: Response,
    db: DB,
    user: CurrentUser,
):
    tz = zone(payload.timezone)
    if payload.worn_on > now_utc().astimezone(tz).date():
        raise HTTPException(422, "Ngày mặc không được ở tương lai")
    lock_user(db, user.id)
    # Quyền được kiểm tra trước cả khi trả lại bản ghi trùng.
    get_owned_session(db, user.id, session_id, lock=True)
    outfit = get_owned_outfit(db, user.id, session_id, outfit_id, lock=True)
    existing = db.scalar(
        select(WearEvent).where(
            WearEvent.user_id == user.id,
            WearEvent.outfit_id == outfit_id,
            WearEvent.worn_on == payload.worn_on,
        )
    )
    if existing:
        response.status_code = 200
        db.commit()
        return {"event": event_response(existing), "new_achievements": []}
    if outfit.revision != payload.expected_revision:
        raise HTTPException(
            409, {"code": "revision_conflict", "message": "Bộ phối đã thay đổi; hãy tải lại."}
        )
    ids = list(
        db.scalars(select(RecommendedItem.garment_id).where(RecommendedItem.outfit_id == outfit_id))
    )
    garments = list(
        db.scalars(
            select(Garment)
            .where(
                Garment.id.in_(ids), Garment.user_id == user.id, Garment.catalog_status == "active"
            )
            .order_by(Garment.id)
            .with_for_update()
        )
    )
    if not ids or len(garments) != len(ids):
        raise HTTPException(409, "Bộ phối không còn hợp lệ")
    event = WearEvent(
        user_id=user.id,
        session_id=session_id,
        outfit_id=outfit_id,
        revision=outfit.revision,
        item_ids=ids,
        worn_on=payload.worn_on,
        timezone=payload.timezone,
    )
    db.add(event)
    for garment in garments:
        garment.wear_count += 1
        garment.last_worn_at = now_utc()
    new = sync_progress(db, user.id)
    db.commit()
    current_week = str(week_start(now_utc().astimezone(tz).date()))
    new = [key for key in new if ":" not in key or key.startswith(current_week + ":")]
    return {"event": event_response(event), "new_achievements": new}


@router.get("/wear-events")
def list_wear(
    db: DB, user: CurrentUser, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return [
        event_response(e)
        for e in db.scalars(
            select(WearEvent)
            .where(WearEvent.user_id == user.id)
            .order_by(WearEvent.created_at.desc(), WearEvent.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/impact")
def summary(db: DB, user: CurrentUser, timezone: str = Query(max_length=100)):
    today = now_utc().astimezone(zone(timezone)).date()
    start = week_start(today)
    wardrobe = set(
        db.scalars(
            select(Garment.id).where(Garment.user_id == user.id, Garment.catalog_status == "active")
        )
    )
    outfits = list(
        db.scalars(
            select(RecommendedOutfit)
            .join(RecommendationSession)
            .where(RecommendationSession.user_id == user.id)
        )
    )
    valid = []
    covered = set()
    for outfit in outfits:
        ids = set(
            db.scalars(
                select(RecommendedItem.garment_id).where(RecommendedItem.outfit_id == outfit.id)
            )
        )
        if ids and ids <= wardrobe:
            valid.append(outfit.id)
            covered.update(ids)
    events = list(db.scalars(select(WearEvent).where(WearEvent.user_id == user.id)))
    worn = {i for e in events for i in e.item_ids}
    state = db.get(ImpactProgress, user.id)
    data = state.data if state else {}
    unlocked = data.get("badges", {})
    counts = {"items": len(wardrobe), "outfits": len(data.get("outfits", [])), "wear": len(events)}
    selected = [e for e in events if start <= e.worn_on <= start + timedelta(days=6)]
    progress = {
        "days": len({e.worn_on for e in selected}),
        "outfits": len({e.outfit_id for e in selected}),
        "items": len({i for e in selected for i in e.item_ids}),
    }

    def percent(count):
        return int(count * 100 / len(wardrobe) + 0.5) if wardrobe else 0

    return {
        "itemCount": len(wardrobe),
        "outfitCount": len(valid),
        "coveragePercent": percent(len(covered)),
        "wearCount": len(events),
        "wornItemCount": len(worn),
        "utilizationPercent": percent(len(worn & wardrobe)),
        "badges": [
            {
                "id": key,
                "title": title,
                "threshold": target,
                "progress": target if key in unlocked else min(target, counts[kind]),
                "unlocked": key in unlocked,
                "unlockedAt": unlocked.get(key),
            }
            for key, title, target, kind in BADGES
        ],
        "week": {
            "start": str(start),
            "end": str(start + timedelta(days=6)),
            "tasks": [
                {
                    "id": key,
                    "title": title,
                    "target": target,
                    "progress": min(target, progress[key]),
                    "completed": progress[key] >= target,
                }
                for key, title, target in TASKS
            ],
        },
        "serverTime": now_utc().isoformat(),
    }
