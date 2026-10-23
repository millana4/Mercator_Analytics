# ---------- Анализ поисковой выдачи сета: снимки -> повторяемость, индикатор, погода -> база ----------
import logging

import pandas as pd
from sqlalchemy.orm import Session

from app.analytics.algorithms.search_dynamics import (STATUS_FEW_STRATA, STATUS_LOW, repeatability_24h,
                                           repeatability_indicator, weather_for_runs)
from app.analytics.weather import HOURLY_COLUMNS, fetch_hourly, load_weather
from app.config import policies
from app.db.repository import save_search_points

logger = logging.getLogger(__name__)


def attach_weather(ind: pd.DataFrame, weather_hourly: pd.DataFrame,
                   strata_total: int) -> pd.DataFrame:
    """Индикатор + погода за сутки до каждого снимка + число страт сета."""
    weather = weather_for_runs(weather_hourly, ind["run_at"])
    points = ind.merge(weather, on="run_at", how="left")
    points["strata_total"] = strata_total
    return points


def run_search_for_set(connector, session: Session, set_row: pd.Series,
                       fetch=fetch_hourly) -> int:
    """Пересчитывает ряд выдачи сета целиком и записывает его. Возвращает число точек.
    fetch - загрузчик погоды (в тестах подменяется)."""
    set_id, geo = set_row["set_id"], set_row["geo"]
    label = f"{set_row['category']} / {geo} (set_id={set_id})"

    strata_total = len(connector.get_strata(set_id))
    snapshots = connector.get_search_snapshots(set_id)
    ind = repeatability_indicator(repeatability_24h(snapshots, strata_total))

    if len(ind):
        start = ind["run_at"].min() - pd.Timedelta(hours=policies.WEATHER_WINDOW_HOURS)
        weather = load_weather(session, geo, start, ind["run_at"].max(), fetch=fetch)
    else:
        weather = pd.DataFrame(columns=HOURLY_COLUMNS)

    points = attach_weather(ind, weather, strata_total)
    saved = save_search_points(session, points, set_id=set_id,
                               marketplace=set_row["marketplace"],
                               category=set_row["category"], geo=geo)
    shown = int((points["status"] != STATUS_FEW_STRATA).sum())
    low = int((points["status"] == STATUS_LOW).sum())
    logger.info("Выдача %s: плиток %d, точек %d (показываемых %d, ниже обычного %d), страт в сете %d",
                label, len(snapshots), saved, shown, low, strata_total)
    return saved