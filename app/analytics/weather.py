# ---------- Погода к снимкам выдачи: Open-Meteo, почасовой кеш в weather_hourly ----------
import logging
from datetime import date, datetime, time as dtime, timedelta

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import policies
from app.db.models import WeatherHourly

logger = logging.getLogger(__name__)

# time - местное время; temp °C; precip мм за час; cloud - облачность %; code - код погоды WMO
HOURLY_COLUMNS = ["time", "temp", "precip", "cloud", "code"]
FLOAT_COLUMNS = ["temp", "precip", "cloud", "code"]
OPEN_METEO_FIELDS = {"temp": "temperature_2m", "precip": "precipitation",
                     "cloud": "cloud_cover", "code": "weather_code"}


def parse_hourly(payload: dict) -> pd.DataFrame:
    """Ответ Open-Meteo -> HOURLY_COLUMNS. Пропуски в рядах - NaN."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    n = len(times)
    df = pd.DataFrame({"time": pd.to_datetime(times)})
    for col, field in OPEN_METEO_FIELDS.items():
        df[col] = hourly.get(field) or [np.nan] * n
    df[FLOAT_COLUMNS] = df[FLOAT_COLUMNS].astype("float64")
    return df[HOURLY_COLUMNS]


def fetch_hourly(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """Почасовая погода с start по end включительно, московское время.
    trust_env=False: прокси из окружения (VPN на ПК) не используется."""
    with httpx.Client(timeout=30, trust_env=False) as client:
        resp = client.get(policies.WEATHER_API_URL, params={
            "latitude": lat, "longitude": lon,
            "hourly": ",".join(OPEN_METEO_FIELDS.values()),
            "timezone": "Europe/Moscow",
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        })
        resp.raise_for_status()
        return parse_hourly(resp.json())


def fetch_range(cached_min: date | None, cached_max: date | None, start: date, end: date,
                refresh_days: int) -> tuple[date, date]:
    """Какие сутки докачать: кеш не покрывает start - всё с start; иначе - от последних
    refresh_days суток кеша (их Open-Meteo уточняет) до end."""
    if cached_min is None or cached_max is None or cached_min > start:
        return start, end
    return max(start, cached_max - timedelta(days=refresh_days)), end


def load_weather(session: Session, geo: str, start: datetime, end: datetime,
                 fetch=fetch_hourly) -> pd.DataFrame:
    """Почасовая погода гео за [start, end]: докачка недостающего в кеш и чтение из кеша.
    Open-Meteo недоступен - предупреждение и то, что уже есть в кеше."""
    coords = policies.GEO_COORDS.get(geo)
    if coords is None:
        logger.warning("Погода: нет координат для гео %s - погода снимков пустая", geo)
        return pd.DataFrame(columns=HOURLY_COLUMNS)

    cached_min, cached_max = session.execute(
        select(func.min(WeatherHourly.time), func.max(WeatherHourly.time))
        .where(WeatherHourly.geo == geo)).one()
    day_from, day_to = fetch_range(cached_min.date() if cached_min else None,
                                   cached_max.date() if cached_max else None,
                                   start.date(), end.date(), policies.WEATHER_REFRESH_DAYS)
    try:
        fresh = fetch(coords[0], coords[1], day_from, day_to)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning("Погода: Open-Meteo недоступен (%s) - берём кеш", e)
        fresh = None

    if fresh is not None and not fresh.empty:
        session.execute(delete(WeatherHourly).where(
            WeatherHourly.geo == geo,
            WeatherHourly.time >= datetime.combine(day_from, dtime.min),
            WeatherHourly.time < datetime.combine(day_to + timedelta(days=1), dtime.min)))
        session.add_all([WeatherHourly(geo=geo, time=row.time.to_pydatetime(),
                                       temp=_f(row.temp), precip=_f(row.precip),
                                       cloud=_f(row.cloud), code=_i(row.code))
                         for row in fresh.itertuples()])
        session.flush()
        logger.info("Погода %s: загружено часов %d (%s - %s)", geo, len(fresh), day_from, day_to)

    return read_weather(session, geo, start, end)


def read_weather(session: Session, geo: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Почасовая погода гео за [start, end] из кеша, без обращения к Open-Meteo."""
    rows = session.execute(
        select(WeatherHourly.time, WeatherHourly.temp, WeatherHourly.precip,
               WeatherHourly.cloud, WeatherHourly.code)
        .where(WeatherHourly.geo == geo, WeatherHourly.time >= start, WeatherHourly.time <= end)
        .order_by(WeatherHourly.time)).all()
    df = pd.DataFrame(rows, columns=HOURLY_COLUMNS)
    df[FLOAT_COLUMNS] = df[FLOAT_COLUMNS].astype("float64")
    return df


def _f(value) -> float | None:
    return None if pd.isna(value) else float(value)


def _i(value) -> int | None:
    return None if pd.isna(value) else int(value)