from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from garblo.config import Settings

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Mật khẩu không được vượt quá 72 byte UTF-8")
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    # bcrypt cũ âm thầm cắt suffix; không chấp nhận password đã bị cắt.
    if len(password.encode("utf-8")) > 72:
        return False
    return password_context.verify(password, password_hash)


def create_access_token(subject: str, settings: Settings) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_minutes)
    payload = {"sub": subject, "exp": expires_at, "typ": "access"}
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    ), expires_at


def decode_access_token(token: str, settings: Settings) -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) and payload.get("typ") == "access" else None
