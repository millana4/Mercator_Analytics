# ---------- Точки выдачи: индикатор + погода + число страт сета ----------
import pandas as pd

from app.analytics.algorithms.search_dynamics import REPEAT_COLUMNS, repeatability_indicator
from app.analytics.search_service import attach_weather

RUN = pd.Timestamp("2026-10-21 18:37")


def test_attach_weather_and_strata_total():
    rep = pd.DataFrame([{"run_at": RUN, "prev_run": RUN - pd.Timedelta(days=1), "strata_n": 50,
                         "repeat_pct": 44.0, "enough_strata": True}])
    hourly = pd.DataFrame({"time": pd.date_range("2026-10-20 18:00", periods=26, freq="h"),
                           "temp": 3.0, "precip": 0.0})
    pts = attach_weather(repeatability_indicator(rep), hourly, 57)
    p = pts.iloc[0]
    assert p["status"] == "warmup"
    assert p["temp_mean"] == 3.0 and p["temp_now"] == 3.0
    assert p["strata_total"] == 57


def test_attach_weather_empty():
    ind = repeatability_indicator(pd.DataFrame(columns=REPEAT_COLUMNS))
    pts = attach_weather(ind, pd.DataFrame(columns=["time", "temp", "precip"]), 57)
    assert pts.empty
    assert {"temp_mean", "strata_total", "status"} <= set(pts.columns)