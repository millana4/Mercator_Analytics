# ---------- Анализ поисковой выдачи: ряд повторяемости и погода для дашборда ----------
from datetime import datetime, time, timedelta

import pandas as pd
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analytics.algorithms.search_dynamics import (STATUS_FEW_STRATA, STATUS_LOW,
                                                      weather_for_runs)
from app.analytics.weather import read_weather
from app.config import policies
from app.db import repository as repo
from app.db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["search-dynamics"])

WEATHER_FIELDS = ("temp_mean", "temp_now", "temp_min", "temp_max", "precip")


@router.get("/search-dynamics")
def search_dynamics(category: str, geo: str, marketplace: str,
                    session: Session = Depends(get_session)) -> dict:
    """Ряд повторяемости выдачи за сутки и погода.

    points - только точки с достаточным числом общих страт; alert - индикатор
    «ниже обычного» (выдача изменилась сильнее обычного), band_low / band_high -
    коридор обычной повторяемости, %. ready - точек не меньше SEARCH_MIN_POINTS,
    иначе блок на дашборде не показывается.
    weather - на каждые сутки от первой до последней точки, на момент суточного
    снимка: и там, где повторяемость не посчитана."""
    rows = repo.list_search_points(session, category=category, geo=geo, marketplace=marketplace)
    points = [p for p in rows if p["status"] != STATUS_FEW_STRATA and p["repeat_pct"] is not None]
    return {
        "ready": len(points) >= policies.SEARCH_MIN_POINTS,
        "points": [{
            "run_at": p["run_at"], "prev_run": p["prev_run"],
            "repeat_pct": p["repeat_pct"], "status": p["status"],
            "alert": p["status"] == STATUS_LOW,
            "band_low": p["band_low"], "band_high": p["band_high"],
            "strata_n": p["strata_n"], "strata_total": p["strata_total"],
        } for p in points],
        "weather": _daily_weather(session, geo, points),
    }


def _daily_weather(session: Session, geo: str, points: list[dict]) -> list[dict]:
    """Погода за сутки до суточного снимка на каждые сутки ряда - из кеша weather_hourly."""
    if not points:
        return []
    hour, minute = policies.SEARCH_DAILY_TIME
    first, last = points[0]["run_at"].date(), points[-1]["run_at"].date()
    days = [datetime.combine(first + timedelta(days=i), time(hour, minute))
            for i in range((last - first).days + 1)]
    hourly = read_weather(session, geo,
                          days[0] - timedelta(hours=policies.WEATHER_WINDOW_HOURS), days[-1])
    weather = weather_for_runs(hourly, days)
    return [{"run_at": r.run_at.to_pydatetime(), **{k: _f(getattr(r, k)) for k in WEATHER_FIELDS}}
            for r in weather.itertuples()]


def _f(value) -> float | None:
    return None if pd.isna(value) else float(value)