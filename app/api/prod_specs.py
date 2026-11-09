# ---------- Рекомендации по закупкам: страты с готовым анализом и сам анализ ----------
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analytics import active_sets
from app.config import policies
from app.db.session import get_session
from app.prod_specs import store, view

router = APIRouter(prefix="/api/v1/prod_specs", tags=["prod-specs"])


@router.get("/strata")
def strata(category: str, geo: str, marketplace: str,
           session: Session = Depends(get_session)) -> dict:
    """Страты активного сета связки с готовым анализом - для выпадающего списка.
    Категория не включена в PROD_SPECS_CATEGORIES - пусто. ready - есть хотя бы одна страта,
    иначе блок на дашборде не показывается."""
    if category not in policies.PROD_SPECS_CATEGORIES:
        return {"ready": False, "strata": []}
    rows = store.list_analyses(session, category=category, geo=geo, marketplace=marketplace,
                               set_ids=active_sets.active_set_ids(category, geo, marketplace))
    items = [view.list_item(r) for r in rows]
    return {"ready": bool(items), "strata": items}


@router.get("/strata/{stratum_id}")
def stratum(stratum_id: UUID, session: Session = Depends(get_session)) -> dict:
    """Анализ страты: кластеры (название, описание, признаки с частотами, карточки)
    и карточки «Прочее». Не активный сет или не включённая категория - 404."""
    row = store.get_analysis(session, str(stratum_id))
    if row is None or not _visible(row):
        raise HTTPException(404, "Анализ страты не найден")
    return view.detail_view(row)


def _visible(row) -> bool:
    if row.category not in policies.PROD_SPECS_CATEGORIES:
        return False
    shown = active_sets.active_set_ids(row.category, row.geo, row.marketplace)
    return shown is None or str(row.source_set_id) in shown