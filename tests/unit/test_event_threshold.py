# ---------- Порог события по биномиальному правилу ----------
import numpy as np
import pandas as pd
import pytest

from app.analytics.algorithms.common import detect_events, event_threshold


# Таблицы расчёта размера выборки: фон 5%, ложная тревога <= 1%
@pytest.mark.parametrize("n, k", [(10, 4), (15, 4), (20, 5), (25, 5), (30, 6), (40, 7),
                                  (50, 8), (60, 8), (100, 12), (150, 15), (200, 19),
                                  (250, 22), (300, 25)])
def test_threshold_matches_sample_size_tables(n, k):
    assert event_threshold(n, 0.05, 0.01) == k


# Измеренный фон 22.10: EWMA 0,0608 / 0,0589, MACD 0,0116
@pytest.mark.parametrize("n, p0, k", [(30, 0.0608, 6), (100, 0.0608, 13), (200, 0.0608, 22),
                                      (200, 0.0589, 21), (30, 0.0116, 3), (100, 0.0116, 5),
                                      (200, 0.0116, 7)])
def test_threshold_for_measured_background(n, p0, k):
    assert event_threshold(n, p0, 0.01) == k


def test_threshold_degenerate_inputs():
    assert event_threshold(0, 0.05, 0.01) == 1
    assert event_threshold(30, 0.0, 0.01) == 1


START = pd.Timestamp("2026-10-20 00:37")


def _slice(n_cards: int, flagged: int) -> pd.DataFrame:
    """n_cards карточек, 3 точки; в средней точке у первых flagged карточек флаг роста."""
    rows = []
    for i in range(n_cards):
        for j in range(3):
            up = j == 1 and i < flagged
            rows.append({"sku": i, "parsed_at": START + pd.Timedelta(hours=12 * j),
                         "up_flag": up, "down_flag": False,
                         "window_return": 0.2 if up else 0.0,
                         "velocity_per_day": 0.05 if up else 0.0})
    return pd.DataFrame(rows)


def _events(df: pd.DataFrame) -> pd.DataFrame:
    return detect_events(df, algorithm="EWMA", label="тест", background_rate=0.05,
                         false_alarm=0.01, min_cards=26, n_bootstrap=100, ci_alpha=0.05,
                         rng=np.random.default_rng(1))


def test_below_threshold_no_event():
    assert _events(_slice(30, 5)).empty


def test_at_threshold_event():
    events = _events(_slice(30, 6))
    assert len(events) == 1
    e = events.iloc[0]
    assert e["direction"] == "up"
    assert e["peak_at"] == START + pd.Timedelta(hours=12)
    assert e["participation_pct"] == pytest.approx(6 / 30)


def test_points_below_min_cards_ignored():
    # 20 карточек при минимуме среза 26 - точек для событий нет
    assert _events(_slice(20, 20)).empty