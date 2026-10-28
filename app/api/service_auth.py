# ---------- Межсервисный ключ: входящие запросы от ядра ----------
import hmac

from fastapi import Header, HTTPException

from app.config.settings import get_settings


def require_service_key(authorization: str | None = Header(default=None)) -> None:
    """Authorization: Bearer <SERVICE_API_KEY> - тот же ключ, что PARSER_API_KEY ядра.
    Ключ в настройках не задан - эндпоинт закрыт (503), а не открыт для всех."""
    expected = get_settings().service_api_key
    if not expected:
        raise HTTPException(503, "Межсервисный ключ не настроен")
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Нет межсервисного ключа")
    if not hmac.compare_digest(authorization[len("Bearer "):].encode(), expected.encode()):
        raise HTTPException(401, "Неверный межсервисный ключ")