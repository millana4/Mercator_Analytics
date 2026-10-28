# ---------- Сезонная замена сета: показ только активного сета, склейка выдачи, готовность ----------
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd

from app.analytics import active_sets
from app.config.settings import get_settings
from app.db import repository as repo

KIDS = {"marketplace": "ozon", "category": "kids_clothing", "geo": "Санкт-Петербург"}
KEY = "test-service-key"


def _result(set_id, *, algorithm="EWMA", n_cards=150, sufficient=True,
            level="category", level_key="kids_clothing"):
    return SimpleNamespace(
        set_id=set_id, category="kids_clothing", level=level, level_key=level_key,
        algorithm=algorithm, n_cards=n_cards, total_quota=205, deficit_share=0.0,
        scale_factor=1.0, sufficient=sufficient,
        insufficient_reason=None if sufficient else "пригодных карточек мало",
        warning=None, events=pd.DataFrame(), curve=pd.DataFrame())


def _save(db_session, result, grid="2026-11-01 00:37"):
    repo.save_run(db_session, result, marketplace=KIDS["marketplace"], geo=KIDS["geo"],
                  grid_point_at=pd.Timestamp(grid))
    db_session.flush()


# ---------- Показ только активного сета ----------
def test_latest_run_and_levels_filtered_by_set(db_session):
    old, new = str(uuid4()), str(uuid4())
    _save(db_session, _result(old, n_cards=180))
    _save(db_session, _result(new, n_cards=60, sufficient=False))

    run = repo.latest_run(db_session, level="category", level_key="kids_clothing",
                          algorithm="EWMA", set_ids=[old], **KIDS)
    assert run.n_cards == 180
    levels = repo.list_levels(db_session, level="category", set_ids=[new], **KIDS)
    assert [(r["n_cards"], r["sufficient"]) for r in levels] == [(60, False)]
    assert repo.latest_run(db_session, level="category", level_key="kids_clothing",
                           algorithm="EWMA", set_ids=[], **KIDS) is None


def test_api_snapshot_shows_active_set(client, db_session, monkeypatch):
    old, new = str(uuid4()), str(uuid4())
    _save(db_session, _result(old, n_cards=180))
    _save(db_session, _result(new, n_cards=60))
    params = {**KIDS, "level_key": "kids_clothing", "algorithm": "EWMA"}

    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a: [old])
    assert client.get("/api/v1/snapshot", params=params).json()["meta"]["n_cards"] == 180
    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a: [new])      # переключение
    assert client.get("/api/v1/snapshot", params=params).json()["meta"]["n_cards"] == 60


def test_api_niche_level_not_filtered_by_set(client, db_session, monkeypatch):
    niche_id = str(uuid4())
    _save(db_session, _result(niche_id, level="niche", level_key=niche_id))

    def no_lookup(*a):
        raise AssertionError("у ниш фильтра по сету нет")
    monkeypatch.setattr(active_sets, "active_set_ids", no_lookup)
    r = client.get("/api/v1/snapshot", params={**KIDS, "level": "niche", "level_key": niche_id,
                                               "algorithm": "EWMA"})
    assert r.status_code == 200


# ---------- Склейка ряда выдачи ----------
def _point(run_at: str) -> dict:
    run = pd.Timestamp(run_at)
    return {"run_at": run, "prev_run": run - pd.Timedelta(days=1), "strata_n": 50,
            "strata_total": 57, "repeat_pct": 45.0, "normal_pct": None, "band_low": None,
            "band_high": None, "z": None, "status": "warmup", "temp_mean": 5.0, "temp_now": 4.0,
            "temp_min": 2.0, "temp_max": 7.0, "precip": 0.0}


def test_search_points_glued_by_windows(db_session):
    old, new = str(uuid4()), str(uuid4())
    repo.save_search_points(db_session, pd.DataFrame([_point("2026-10-12 18:37"),
                                                      _point("2026-10-14 18:37")]),
                            set_id=old, **KIDS)
    repo.save_search_points(db_session, pd.DataFrame([_point("2026-10-13 18:37"),
                                                      _point("2026-10-15 18:37")]),
                            set_id=new, **KIDS)
    switch = pd.Timestamp("2026-10-14 01:15").to_pydatetime()
    windows = [(old, pd.Timestamp("2026-09-08").to_pydatetime(), switch), (new, switch, None)]

    pts = repo.list_search_points(db_session, windows=windows, **KIDS)
    assert [p["run_at"] for p in pts] == [pd.Timestamp("2026-10-12 18:37"),
                                          pd.Timestamp("2026-10-15 18:37")]
    assert repo.list_search_points(db_session, windows=[], **KIDS) == []
    assert len(repo.list_search_points(db_session, **KIDS)) == 4          # без склейки - все


# ---------- Готовность сета для ядра ----------
def test_readiness_requires_service_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_api_key", KEY)
    url = f"/api/v1/internal/sets/{uuid4()}/readiness"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_readiness_closed_without_configured_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_api_key", "")
    r = client.get(f"/api/v1/internal/sets/{uuid4()}/readiness",
                   headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503


def test_readiness_by_latest_category_run(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "service_api_key", KEY)
    set_id = str(uuid4())
    headers = {"Authorization": f"Bearer {KEY}"}
    url = f"/api/v1/internal/sets/{set_id}/readiness"

    body = client.get(url, headers=headers).json()
    assert body["ready"] is False and body["algorithms"] == {"EWMA": None, "MACD": None}

    _save(db_session, _result(set_id, algorithm="EWMA", n_cards=150), grid="2026-11-01 00:37")
    _save(db_session, _result(set_id, algorithm="MACD", n_cards=80, sufficient=False),
          grid="2026-11-01 00:37")
    body = client.get(url, headers=headers).json()
    assert body["algorithms"]["EWMA"]["sufficient"] is True
    assert body["ready"] is False                                        # EWMA уже, MACD ещё нет

    _save(db_session, _result(set_id, algorithm="MACD", n_cards=120), grid="2026-11-03 12:37")
    body = client.get(url, headers=headers).json()
    assert body["ready"] is True
    assert body["algorithms"]["MACD"]["n_cards"] == 120