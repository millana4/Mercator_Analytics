# ---------- Погода: разбор ответа Open-Meteo и диапазон докачки ----------
from datetime import date

import pandas as pd

from app.analytics.weather import HOURLY_COLUMNS, fetch_range, parse_hourly


def test_parse_hourly():
    payload = {"hourly": {"time": ["2026-10-22T18:00", "2026-10-22T19:00"],
                          "temperature_2m": [5.4, None], "precipitation": [0.0, 0.3],
                          "cloud_cover": [90, 40], "weather_code": [3, 61]}}
    df = parse_hourly(payload)
    assert list(df.columns) == HOURLY_COLUMNS
    assert df["time"].tolist() == [pd.Timestamp("2026-10-22 18:00"), pd.Timestamp("2026-10-22 19:00")]
    assert df["temp"].iloc[0] == 5.4 and pd.isna(df["temp"].iloc[1])
    assert df["precip"].tolist() == [0.0, 0.3]
    assert df["cloud"].tolist() == [90.0, 40.0]
    assert df["code"].tolist() == [3.0, 61.0]


def test_parse_hourly_without_new_fields():
    # в ответе нет облачности и кода - колонки пустые, а не ошибка
    df = parse_hourly({"hourly": {"time": ["2026-10-22T18:00"], "temperature_2m": [5.0],
                                  "precipitation": [0.0]}})
    assert pd.isna(df["cloud"].iloc[0]) and pd.isna(df["code"].iloc[0])


def test_parse_hourly_empty():
    assert parse_hourly({}).empty


def test_fetch_range_empty_cache():
    assert fetch_range(None, None, date(2026, 10, 5), date(2026, 10, 23), 3) == \
        (date(2026, 10, 5), date(2026, 10, 23))


def test_fetch_range_cache_starts_later():
    # кеш с 10.10, а нужен с 05.10 - качаем всё заново
    assert fetch_range(date(2026, 10, 10), date(2026, 10, 22), date(2026, 10, 5),
                       date(2026, 10, 23), 3) == (date(2026, 10, 5), date(2026, 10, 23))


def test_fetch_range_refreshes_last_days():
    assert fetch_range(date(2026, 10, 1), date(2026, 10, 22), date(2026, 10, 5),
                       date(2026, 10, 23), 3) == (date(2026, 10, 19), date(2026, 10, 23))