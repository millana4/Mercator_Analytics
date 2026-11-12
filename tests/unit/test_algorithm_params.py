# ---------- Параметры алгоритмов: окно в шагах и фон по уровням ----------
import numpy as np
import pandas as pd
import pytest

from app.analytics.algorithms import ewma, macd

PARAMS_BY_ALGORITHM = [
    (ewma, 3, 0.1273, 0.2015),    # окно 1,5 суток = 3 шага; фон категории и подкатегории
    (macd, 4, 0.0489, 0.1045),    # окно 2 суток = 4 шага
]


@pytest.mark.parametrize("module, steps, category_rate, small_rate", PARAMS_BY_ALGORITHM)
def test_window_steps_is_integer(module, steps, category_rate, small_rate):
    assert module.PARAMS.window_steps == steps
    assert isinstance(module.PARAMS.window_steps, int)


@pytest.mark.parametrize("module, steps, category_rate, small_rate", PARAMS_BY_ALGORITHM)
def test_background_rate_by_level(module, steps, category_rate, small_rate):
    p = module.PARAMS
    assert p.background_rate_for("category") == category_rate
    assert p.background_rate_for("subcategory") == small_rate
    assert p.background_rate_for("niche") == small_rate


@pytest.mark.parametrize("module, steps, category_rate, small_rate", PARAMS_BY_ALGORITHM)
@pytest.mark.parametrize("level", ["category", "subcategory", "niche"])
def test_run_passes_background_of_level(monkeypatch, module, steps, category_rate, small_rate, level):
    seen = {}

    def fake_detect_events(df, **kwargs):
        seen["background_rate"] = kwargs["background_rate"]
        return pd.DataFrame()

    monkeypatch.setattr(module, "detect_events", fake_detect_events)
    points = pd.date_range("2026-08-01 00:37", periods=12, freq="12h")
    df = pd.DataFrame([{"sku": f"s{i}", "parsed_at": t, "log_price": np.log(1000.0 + i)}
                       for i in range(6) for t in points])
    module.run(df, label="тест", level=level)
    assert seen["background_rate"] == (category_rate if level == "category" else small_rate)