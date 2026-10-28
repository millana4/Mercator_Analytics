# ---------- Эндпоинты для ядра ----------
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.service_auth import require_service_key
from app.db import repository as repo
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/internal", tags=["internal"],
                   dependencies=[Depends(require_service_key)])


@router.get("/sets/{set_id}/readiness")
def set_readiness(set_id: UUID, session: Session = Depends(get_session)) -> dict:
    """Готовность аналитики сета на уровне категории: последний расчёт каждого
    действующего алгоритма. ready - MACD выполнился (по нему ядро переключает
    категорию на новый сезонный сет)."""
    algorithms = repo.set_readiness(session, str(set_id))
    macd = algorithms.get("MACD")
    return {"set_id": str(set_id), "algorithms": algorithms,
            "ready": bool(macd is not None and macd["sufficient"])}