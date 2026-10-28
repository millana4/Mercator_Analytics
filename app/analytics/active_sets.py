# ---------- Какой сет связки показывать: статусы и активация сетов из ядра ----------
# Сет ядра: draft (черновик) -> collecting (идёт сбор, считается, не показывается) ->
# active (показывается) -> retired (выведен, его ряд выдачи - история до переключения).
import logging
from datetime import datetime
from functools import lru_cache

import pandas as pd

logger = logging.getLogger(__name__)

SHOWN_STATUS = "active"
CALC_STATUSES = ("active", "collecting")
HISTORY_STATUSES = ("active", "retired")

# Окно ряда выдачи сета: (set_id, с какого момента, до какого; None - без конца)
Window = tuple[str, datetime, datetime | None]


def _combo(sets: pd.DataFrame, category: str, geo: str, marketplace: str) -> pd.DataFrame:
    return sets[(sets["category"] == category) & (sets["geo"] == geo)
                & (sets["marketplace"] == marketplace)]


def active_ids_from_sets(sets: pd.DataFrame, category: str, geo: str,
                         marketplace: str) -> list[str]:
    """id активных сетов связки (обычно один)."""
    combo = _combo(sets, category, geo, marketplace)
    return combo.loc[combo["status"] == SHOWN_STATUS, "set_id"].astype(str).tolist()


def windows_from_sets(sets: pd.DataFrame, category: str, geo: str,
                      marketplace: str) -> list[Window]:
    """Склейка ряда выдачи: сет показывается от своей активации до активации следующего.
    Выведенные сеты дают историю до переключения, активный - от переключения и дальше."""
    combo = _combo(sets, category, geo, marketplace)
    hist = combo[combo["status"].isin(HISTORY_STATUSES)].sort_values(
        "activated_at", na_position="first", kind="stable")
    rows = list(hist.itertuples(index=False))
    windows: list[Window] = []
    for i, r in enumerate(rows):
        start = r.activated_at.to_pydatetime() if pd.notna(r.activated_at) else datetime.min
        nxt = rows[i + 1].activated_at if i + 1 < len(rows) else None
        end = nxt.to_pydatetime() if nxt is not None and pd.notna(nxt) else None
        windows.append((str(r.set_id), start, end))
    return windows


@lru_cache(maxsize=1)
def _connector():
    """Один коннектор на процесс: статусы сетов читаются на каждый запрос API."""
    from app.connectors.factory import get_connector
    return get_connector()


def _sets() -> pd.DataFrame | None:
    try:
        return _connector().get_sets()
    except Exception as e:
        logger.warning("Статусы сетов из ядра недоступны - фильтр по активному сету не применяется: %s", e)
        return None


def active_set_ids(category: str, geo: str, marketplace: str) -> list[str] | None:
    """id активных сетов связки; None - ядро недоступно, фильтровать нечем."""
    sets = _sets()
    return None if sets is None else active_ids_from_sets(sets, category, geo, marketplace)


def search_windows(category: str, geo: str, marketplace: str) -> list[Window] | None:
    """Окна склейки ряда выдачи связки; None - ядро недоступно, ряд без склейки."""
    sets = _sets()
    return None if sets is None else windows_from_sets(sets, category, geo, marketplace)