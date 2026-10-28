# ---------- Админка задания: шлюз в ядро (только администратор) ----------
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.auth import require_admin
from app.core_api.client import CoreUnavailable, get_core_client
from app.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# Путь админки ядра: сегменты из латиницы, цифр, дефиса и подчёркивания (id - uuid).
# Остальное (точки, «..», спецсимволы) не пересылается.
SAFE_PATH = re.compile(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*")
CHANGING_METHODS = ("POST", "PATCH", "DELETE")


@router.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def proxy(path: str, request: Request, user: User = Depends(require_admin)) -> Response:
    """Запрос администратора - в админское API ядра (/api/v1/admin/<path>) с межсервисным
    ключом: метод, параметры и тело как есть. Ответ ядра (данные, 404, 409) - фронтенду
    без изменений. Ядро отклонило ключ или упало - 502, недоступно - 503."""
    if not SAFE_PATH.fullmatch(path):
        raise HTTPException(404, "Нет такого раздела админки")
    body = await request.body()
    if request.method in CHANGING_METHODS:
        logger.info("Админка: %s %s /api/v1/admin/%s", user.username, request.method, path)
    try:
        response = await run_in_threadpool(
            get_core_client().admin_request, request.method, f"/api/v1/admin/{path}",
            dict(request.query_params), body or None)
    except CoreUnavailable as e:
        logger.warning("Админка: ядро недоступно (%s /api/v1/admin/%s): %s", request.method, path, e)
        return JSONResponse({"error": "core_unavailable"}, status_code=503)
    return _to_response(response)


def _to_response(response) -> Response:
    if response.status_code in (401, 403):
        logger.error("Админка: ядро отклонило межсервисный ключ (HTTP %s)", response.status_code)
        return JSONResponse({"error": "core_rejected_service_key"}, status_code=502)
    if response.status_code >= 500:
        logger.error("Админка: ошибка ядра HTTP %s: %s", response.status_code, response.text[:300])
        return JSONResponse({"error": f"core_error_{response.status_code}"}, status_code=502)
    if response.status_code == 204 or not response.content:
        return Response(status_code=response.status_code)
    try:
        body = response.json()
    except ValueError:
        body = {"error": response.text[:300]}
    return JSONResponse(body, status_code=response.status_code)