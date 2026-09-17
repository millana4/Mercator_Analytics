# ---------- Аутентификация: логин и проверка токена ----------
import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.users.models import User
from app.users.security import create_access_token, decode_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer_scheme = HTTPBearer()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    username: str


@router.post("/login")
def login(body: LoginRequest, session: Session = Depends(get_session)) -> LoginResponse:
    user = session.execute(
        select(User).where(User.username == body.username)
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    return LoginResponse(access_token=create_access_token(user.username))


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Зависимость для защищённых эндпоинтов - подключим в шаге 4 к /subscriptions."""
    try:
        username = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Недействительный токен")
    user = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(401, "Пользователь не найден")
    return user


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(username=user.username)