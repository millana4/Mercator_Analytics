# ---------- Проверка пароля и работа с JWT ----------
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config.settings import get_settings


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(username: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """Возвращает username из токена. Бросает jwt.PyJWTError, если токен
    невалиден или просрочен - обработка исключения на стороне вызывающего."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    return payload["sub"]