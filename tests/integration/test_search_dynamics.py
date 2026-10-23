# ---------- Анализ выдачи: расчёт сета, кеш погоды, запись и API ----------
from datetime import date, datetime, time
from uuid import uuid4

import httpx
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.analytics.search_service import run_search_for_set
from app.analytics.weather import load_weather
from app.db import repository as repo
from app.db.models import WeatherHourly

KIDS = {"marketplace": "ozon", "category": "kids_clothing", "geo": "Санкт-Петербург"}


def _fake_fetch(calls: list):
    """Погода без сети: каждый час 5 °C и 0,1 мм; запоминает запрошенные сутки."""
    def fetch(lat, lon, start: date, end: date) -> pd.DataFrame:
        calls.append((start, end))
        times = pd.date_range(datetime.combine(start, time.min),
                              datetime.combine(end, time(23)), freq="h")
        return pd.DataFrame({"time": times, "temp": 5.0, "precip": 0.1, "cloud": 90.0, "code": 61.0})
    return fetch


def _failing_fetch(lat, lon, start, end):
    raise httpx.ConnectError("нет сети")


class FakeConnector:
    def __init__(self, n_strata: int, snapshots: pd.DataFrame):
        self.n_strata = n_strata
        self.snapshots = snapshots

    def get_strata(self, set_id):
        return pd.DataFrame({"stratum_id": [f"s{i}" for i in range(self.n_strata)]})

    def get_search_snapshots(self, set_id, date_from=None):
        return self.snapshots


def _snapshots(days: list[int], n_strata: int = 4) -> pd.DataFrame:
    """Суточные снимки 18:37 за дни октября + утренний 06:37 (не учитывается).
    В страте 3 карточки, две постоянные: повторяемость за сутки 2/3."""
    rows = []
    for d in days:
        for run_at in (pd.Timestamp(2026, 10, d, 18, 37), pd.Timestamp(2026, 10, d, 6, 37)):
            for i in range(n_strata):
                for sku in (f"{i}-a", f"{i}-b", f"{i}-{d}"):
                    rows.append({"run_at": run_at, "stratum_id": f"s{i}", "sku": sku})
    return pd.DataFrame(rows)


# ---------- Расчёт сета ----------
def test_run_search_for_set_writes_points(db_session):
    set_row = pd.Series({"set_id": str(uuid4()), **KIDS})
    conn = FakeConnector(4, _snapshots([20, 21, 22]))
    assert run_search_for_set(conn, db_session, set_row, fetch=_fake_fetch([])) == 2

    pts = repo.list_search_points(db_session, **KIDS)
    assert [p["run_at"] for p in pts] == [datetime(2026, 10, 21, 18, 37),
                                          datetime(2026, 10, 22, 18, 37)]
    assert [p["status"] for p in pts] == ["warmup", "warmup"]
    assert pts[0]["repeat_pct"] == pytest.approx(200 / 3)
    assert (pts[0]["strata_n"], pts[0]["strata_total"]) == (4, 4)
    assert pts[0]["temp_mean"] == 5.0
    assert pts[0]["precip"] == pytest.approx(2.4)          # 24 часа по 0,1 мм


def test_run_search_for_set_replaces_series(db_session):
    set_row = pd.Series({"set_id": str(uuid4()), **KIDS})
    run_search_for_set(FakeConnector(4, _snapshots([20, 21, 22])), db_session, set_row,
                       fetch=_fake_fetch([]))
    run_search_for_set(FakeConnector(4, _snapshots([21, 22])), db_session, set_row,
                       fetch=_fake_fetch([]))
    assert len(repo.list_search_points(db_session, **KIDS)) == 1


# ---------- Кеш погоды ----------
def test_load_weather_caches_and_refreshes(db_session):
    calls: list = []
    start, end = datetime(2026, 10, 10, 18, 37), datetime(2026, 10, 22, 18, 37)

    w = load_weather(db_session, KIDS["geo"], start, end, fetch=_fake_fetch(calls))
    assert calls == [(date(2026, 10, 10), date(2026, 10, 22))]
    assert len(w) == 12 * 24                                # 10.10 19:00 ... 22.10 18:00

    load_weather(db_session, KIDS["geo"], start, end, fetch=_fake_fetch(calls))
    assert calls[1] == (date(2026, 10, 19), date(2026, 10, 22))   # последние 3 суток кеша
    cached = db_session.scalar(select(func.count()).select_from(WeatherHourly)
                               .where(WeatherHourly.geo == KIDS["geo"]))
    assert cached == 13 * 24                                # без дублей

    w = load_weather(db_session, KIDS["geo"], start, end, fetch=_failing_fetch)
    assert len(w) == 12 * 24                                # сети нет - из кеша


# ---------- API ----------
def _point(run_at: str, status: str, repeat_pct: float, **extra) -> dict:
    run = pd.Timestamp(run_at)
    base = {"run_at": run, "prev_run": run - pd.Timedelta(days=1), "strata_n": 50,
            "strata_total": 57, "repeat_pct": repeat_pct, "normal_pct": None, "band_low": None,
            "band_high": None, "z": None, "status": status, "temp_mean": 5.2, "temp_now": 4.0,
            "temp_min": 2.1, "temp_max": 7.3, "precip": 0.4}
    return {**base, **extra}


def test_api_not_ready_without_points(client):
    r = client.get("/api/v1/search-dynamics", params=KIDS)
    assert r.status_code == 200
    assert r.json() == {"ready": False, "points": [], "weather": []}


def test_api_points_alert_and_weather_for_every_day(client, db_session):
    points = pd.DataFrame([
        _point("2026-10-12 18:37", "warmup", 44.0),
        _point("2026-10-13 18:37", "few_strata", 45.8, strata_n=15),
        _point("2026-10-14 18:37", "low", 24.1, normal_pct=44.6, band_low=37.5,
               band_high=51.6, z=-5.8),
    ])
    repo.save_search_points(db_session, points, set_id=str(uuid4()), **KIDS)
    db_session.add_all([WeatherHourly(geo=KIDS["geo"], time=t.to_pydatetime(), temp=5.0, precip=0.1,
                                      cloud=90.0, code=71)
                        for t in pd.date_range("2026-10-11 18:00", "2026-10-14 18:00", freq="h")])
    db_session.flush()

    body = client.get("/api/v1/search-dynamics", params=KIDS).json()
    assert body["ready"] is True
    assert [p["status"] for p in body["points"]] == ["warmup", "low"]   # мало страт - не показываем
    assert [p["alert"] for p in body["points"]] == [False, True]
    low = body["points"][1]
    assert low["run_at"] == "2026-10-14T18:37:00" and low["prev_run"] == "2026-10-13T18:37:00"
    assert (low["band_low"], low["band_high"]) == (37.5, 51.6)
    # погода - на каждые сутки ряда, в том числе 13.10, где повторяемость не посчитана
    assert [w["run_at"] for w in body["weather"]] == [
        "2026-10-12T18:37:00", "2026-10-13T18:37:00", "2026-10-14T18:37:00"]
    assert body["weather"][1]["temp_mean"] == 5.0
    assert body["weather"][1]["precip"] == pytest.approx(2.4)
    assert body["weather"][1]["cloud_mean"] == 90.0
    assert (body["weather"][1]["sky"], body["weather"][1]["precip_kind"]) == ("cloudy", "snow")