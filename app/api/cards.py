# ---------- Реальные замеры одной карточки (не снепшоты, а сырые точки парсера) ----------
from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Query

from app.connectors.factory import get_connector

router = APIRouter(prefix="/api/v1", tags=["cards"])


@router.get("/cards/{sku}/measurements")
def card_measurements(
    sku: int,
    date_from: datetime | None = Query(None, description="Начало периода (включительно)"),
    date_to: datetime | None = Query(None, description="Конец периода (включительно)"),
    days: int = Query(30, ge=1, le=365, description="Фолбэк, если date_from/date_to не заданы"),
) -> list[dict]:
    """Замеры цены одной карточки. При заданных date_from/date_to отдаёт
    ряд строго в этих границах (без домысливания пустых точек, если
    реальная история короче окна). Без них - последние `days` суток
    от последнего доступного замера (обратная совместимость)."""
    connector = get_connector()

    if date_from is not None or date_to is not None:
        # +1 секунда, чтобы правая граница была включительной -
        # коннектор фильтрует по "parsed_at < date_to", а не "<="
        upper = date_to + timedelta(seconds=1) if date_to is not None else None
        df = connector.get_measurements([sku], date_from=date_from, date_to=upper)
    else:
        df = connector.get_measurements([sku])
        if not df.empty:
            cutoff = df["parsed_at"].max() - pd.Timedelta(days=days)
            df = df[df["parsed_at"] >= cutoff]

    if df.empty:
        return []

    df = df.sort_values("parsed_at")
    return [{"parsed_at": row["parsed_at"], "price": _f(row["card_price"])}
            for _, row in df.iterrows()]


def _f(value) -> float | None:
    return None if pd.isna(value) else float(value)