# ---------- Анализ поисковой выдачи: повторяемость, индикатор, погода ----------
import numpy as np
import pandas as pd
import pytest

from app.analytics.algorithms.search_dynamics import (MAD_TO_SIGMA, repeatability_24h,
                                           repeatability_indicator, weather_for_runs)


def _snap(run_at: str, strata: dict[str, list[str]]) -> list[dict]:
    return [{"run_at": pd.Timestamp(run_at), "stratum_id": s, "sku": sku}
            for s, skus in strata.items() for sku in skus]


def _df(*parts: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([row for part in parts for row in part])


# ---------- Повторяемость за сутки ----------
def test_repeatability_on_common_strata():
    snaps = _df(
        _snap("2026-10-10 18:37", {"s1": ["a", "b", "c", "d"], "s2": ["e", "f"]}),
        _snap("2026-10-11 18:37", {"s1": ["a", "b", "x", "y"], "s2": ["e", "f"], "s3": ["z"]}),
    )
    rep = repeatability_24h(snaps, strata_total=2)
    assert len(rep) == 1
    r = rep.iloc[0]
    assert r["prev_run"] == pd.Timestamp("2026-10-10 18:37")
    assert r["strata_n"] == 2                       # s3 только в текущем снимке - не в сравнении
    assert r["repeat_pct"] == pytest.approx(4 / 6 * 100)
    assert r["enough_strata"]


def test_repeatability_with_string_dtype():
    # как отдают коннекторы: sku и stratum_id - строковый тип pandas
    snaps = _df(
        _snap("2026-10-10 18:37", {"s1": ["a", "b", "c", "d"], "s2": ["e", "f"]}),
        _snap("2026-10-11 18:37", {"s1": ["a", "b", "x", "y"], "s2": ["e", "f"]}),
    ).astype({"stratum_id": "string", "sku": "string"})
    rep = repeatability_24h(snaps, strata_total=2)
    assert rep.iloc[0]["repeat_pct"] == pytest.approx(4 / 6 * 100)


def test_morning_snapshots_and_gaps_ignored():
    strata = {"s1": ["a", "b"]}
    snaps = _df(_snap("2026-10-10 18:37", strata), _snap("2026-10-11 06:37", strata),
                _snap("2026-10-11 18:37", strata), _snap("2026-10-13 18:37", strata))
    rep = repeatability_24h(snaps, strata_total=1)
    # утренний снимок не берётся; 13.10 без снимка 12.10 - пары нет
    assert rep["run_at"].tolist() == [pd.Timestamp("2026-10-11 18:37")]


def test_few_common_strata_not_enough():
    snaps = _df(_snap("2026-10-10 18:37", {"s1": ["a"], "s2": ["b"]}),
                _snap("2026-10-11 18:37", {"s1": ["a"], "s2": ["b"]}))
    rep = repeatability_24h(snaps, strata_total=4)   # 2 общих при минимуме ceil(0,75 x 4) = 3
    assert not rep.iloc[0]["enough_strata"]


def test_no_daily_snapshots_empty():
    snaps = _df(_snap("2026-10-10 06:37", {"s1": ["a"]}), _snap("2026-10-11 06:37", {"s1": ["a"]}))
    assert repeatability_24h(snaps, strata_total=1).empty


# ---------- Индикатор ----------
START = pd.Timestamp("2026-10-07 18:37")


def _rep(values: list[tuple[float, bool]]) -> pd.DataFrame:
    return pd.DataFrame([{"run_at": START + pd.Timedelta(days=i),
                          "prev_run": START + pd.Timedelta(days=i - 1),
                          "strata_n": 50 if ok else 12, "repeat_pct": v, "enough_strata": ok}
                         for i, (v, ok) in enumerate(values)])


# Детская одежда 07-17.10: разгон, 11-12.10 мало страт, провал 13-15.10, затем норма
KIDS = [(39.4, True), (44.8, True), (44.3, True), (49.1, True), (52.3, False), (45.8, False),
        (36.2, True), (27.3, True), (24.1, True), (39.6, True), (46.4, True)]


def test_indicator_statuses_on_kids_series():
    ind = repeatability_indicator(_rep(KIDS))
    assert ind["status"].tolist() == (["warmup"] * 4 + ["few_strata"] * 2
                                      + ["low"] * 3 + ["normal", "normal"])


def test_indicator_corridor_on_first_verdict():
    ind = repeatability_indicator(_rep(KIDS))
    r = ind.iloc[6]                                  # 13.10
    sigma = MAD_TO_SIGMA * 2.4                       # MAD окна 39,4; 44,8; 44,3; 49,1
    assert r["normal_pct"] == pytest.approx(44.55)
    assert r["band_low"] == pytest.approx(44.55 - 2 * sigma)
    assert r["band_high"] == pytest.approx(44.55 + 2 * sigma)
    assert r["z"] == pytest.approx((36.2 - 44.55) / sigma)


def test_low_days_not_in_history():
    ind = repeatability_indicator(_rep(KIDS))
    # 16.10: окно - только нормальные дни разгона, провал 13-15.10 норму не сдвинул
    assert ind.iloc[9]["normal_pct"] == pytest.approx(44.55)


def test_scale_floor():
    ind = repeatability_indicator(_rep([(50.0, True)] * 4 + [(47.0, True), (45.0, True)]))
    # разброс окна 0 - берётся нижняя граница 2 п.п.: 47 даёт z = -1,5, 45 - z = -2,5
    assert ind["status"].tolist() == ["warmup"] * 4 + ["normal", "low"]
    assert ind.iloc[4]["band_low"] == pytest.approx(46.0)


def test_indicator_empty():
    assert repeatability_indicator(_rep([])).empty


# ---------- Погода ----------
def test_weather_day_before_snapshot():
    hourly = pd.DataFrame({"time": pd.date_range("2026-10-09 00:00", periods=72, freq="h"),
                           "temp": np.arange(72, dtype=float), "precip": 0.5})
    w = weather_for_runs(hourly, [pd.Timestamp("2026-10-10 18:37"),
                                  pd.Timestamp("2026-10-20 18:37")])
    day = w.iloc[0]                                  # часы 09.10 19:00 ... 10.10 18:00
    assert day["temp_mean"] == pytest.approx((19 + 42) / 2)
    assert (day["temp_min"], day["temp_max"]) == (19.0, 42.0)
    assert day["temp_now"] == 42.0
    assert day["precip"] == pytest.approx(12.0)
    assert w.iloc[1][["temp_mean", "temp_now", "precip"]].isna().all()  # погоды нет


# ---------- Значок погоды: небо и осадки ----------
RUN = pd.Timestamp("2026-10-10 18:37")


def _hourly(cloud: float, precip: float, code: float, temp: float = 5.0) -> pd.DataFrame:
    """Сутки до RUN: все 24 часа одинаковые."""
    return pd.DataFrame({"time": pd.date_range("2026-10-09 19:00", periods=24, freq="h"),
                         "temp": temp, "precip": precip, "cloud": cloud, "code": code})


def _icon(hourly: pd.DataFrame) -> tuple:
    w = weather_for_runs(hourly, [RUN]).iloc[0]
    return w["sky"], w["precip_kind"]


def test_sky_by_cloud_cover():
    assert _icon(_hourly(cloud=10, precip=0, code=0))[0] == "clear"
    assert _icon(_hourly(cloud=50, precip=0, code=2))[0] == "partly"
    assert _icon(_hourly(cloud=70, precip=0, code=2))[0] == "partly"  # граница включительно
    assert _icon(_hourly(cloud=95, precip=0, code=3))[0] == "cloudy"


def test_precip_kind_by_codes():
    assert _icon(_hourly(cloud=95, precip=0.1, code=61)) == ("cloudy", "rain")  # 2,4 мм
    assert _icon(_hourly(cloud=95, precip=0.1, code=73)) == ("cloudy", "snow")
    assert _icon(_hourly(cloud=95, precip=0.01, code=61))[1] == "none"  # 0,24 мм < 0,5


def test_precip_mixed_and_fallback_by_temp():
    mixed = _hourly(cloud=95, precip=0.1, code=61)
    mixed.loc[12:, "code"] = 71
    assert _icon(mixed)[1] == "mixed"
    # код не про осадки (облачно) - тип по температуре часов с осадками
    assert _icon(_hourly(cloud=95, precip=0.1, code=3, temp=-2.0))[1] == "snow"
    assert _icon(_hourly(cloud=95, precip=0.1, code=3, temp=3.0))[1] == "rain"


def test_icon_without_cloud_data():
    # кеш без облачности (как старые строки) - неба нет, осадки по температуре
    hourly = _hourly(cloud=95, precip=0.1, code=3).drop(columns=["cloud", "code"])
    assert _icon(hourly) == (None, "rain")