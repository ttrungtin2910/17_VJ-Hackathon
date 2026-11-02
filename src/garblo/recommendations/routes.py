from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from garblo.config import Settings, get_settings
from garblo.database import get_db
from garblo.dependencies import get_current_user
from garblo.models import User
from garblo.recommendations import rendering, schemas, service

router = APIRouter(prefix="/recommendations", tags=["recommendations"])
DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=schemas.SessionResponse, status_code=201)
def create_recommendation(
    payload: schemas.CreateSessionRequest,
    db: DB,
    user: CurrentUser,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
) -> schemas.SessionResponse:
    return service.create_session(db, user.id, payload, idempotency_key)


@router.get("", response_model=list[schemas.SessionResponse])
def recommendations(
    db: DB,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[schemas.SessionResponse]:
    return service.list_sessions(db, user.id, limit=limit, offset=offset)


@router.get("/{session_id}", response_model=schemas.SessionResponse)
def recommendation(session_id: str, db: DB, user: CurrentUser) -> schemas.SessionResponse:
    return service.session_response(db, user.id, service.get_owned_session(db, user.id, session_id))


@router.post("/{session_id}/outfits/{outfit_id}/edits", response_model=schemas.OutfitResponse)
def edit_outfit(
    session_id: str, outfit_id: str, payload: schemas.EditRequest, db: DB, user: CurrentUser
) -> schemas.OutfitResponse:
    return service.apply_edit(db, user.id, session_id, outfit_id, payload)


@router.put("/{session_id}/outfits/{outfit_id}/feedback", response_model=schemas.FeedbackResponse)
def feedback(
    session_id: str, outfit_id: str, payload: schemas.FeedbackRequest, db: DB, user: CurrentUser
) -> schemas.FeedbackResponse:
    return service.put_feedback(db, user.id, session_id, outfit_id, payload)


@router.get(
    "/{session_id}/outfits/{outfit_id}/render", response_model=rendering.RenderResponse | None
)
def get_outfit_render(session_id: str, outfit_id: str, db: DB, user: CurrentUser):
    return rendering.get_render(db, user.id, session_id, outfit_id)


@router.post(
    "/{session_id}/outfits/{outfit_id}/render",
    response_model=rendering.RenderResponse,
    status_code=202,
)
def render_outfit(
    session_id: str,
    outfit_id: str,
    payload: rendering.RenderRequest,
    db: DB,
    user: CurrentUser,
    settings: Settings = Depends(get_settings),
):
    return rendering.start_render(db, user.id, session_id, outfit_id, payload, settings)
