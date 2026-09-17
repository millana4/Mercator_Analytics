# ---------- Реальные замеры одной карточки (не снепшоты, а сырые точки парсера) ----------
import pandas as pd
from fastapi import APIRouter, Query

from app.connectors.factory import get_connector

router = APIRouter(prefix="/api/v1", tags=["cards"])


@router.get("/cards/{sku}/measurements")
def card_measurements(sku: int, days: int = Query(30, ge=1, le=365)) -> list[dict]:
    """Замеры цены одной карточки за последние `days` суток от последнего
    доступного замера (не от "сегодня" - если ряд короче, отдаём что есть,
    ничего не домысливаем)."""
    connector = get_connector()
    df = connector.get_measurements([sku])
    if df.empty:
        return []

    cutoff = df["parsed_at"].max() - pd.Timedelta(days=days)
    df = df[df["parsed_at"] >= cutoff].sort_values("parsed_at")

    return [{"parsed_at": row["parsed_at"], "price": _f(row["card_price"])}
            for _, row in df.iterrows()]


def _f(value) -> float | None:
    return None if pd.isna(value) else float(value)