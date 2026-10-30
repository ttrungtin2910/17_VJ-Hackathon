from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from garblo.config import Settings, get_settings
from garblo.database import get_db
from garblo.models import User
from garblo.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    user_id = decode_access_token(token, settings)
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token"
        )
    return user


def require_ai_configuration(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.ai_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "provider_not_configured",
                "message": "OpenAI direct chưa được cấu hình",
            },
        )
    return settings
