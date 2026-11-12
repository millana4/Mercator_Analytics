# ---------- Кривая рынка: цепной индекс вместо среднего по срезу ----------
import pandas as pd

from app.analytics.algorithms.common import market_curve

T = pd.date_range("2026-11-01 00:37", periods=6, freq="12h")


def _panel(levels: dict[str, list]) -> pd.DataFrame:
    """levels: sku -> уровень в каждой из шести точек (None - карточки в точке ещё нет)."""
    rows = [{"sku": sku, "parsed_at": t, "level": v}
            for sku, values in levels.items() for t, v in zip(T, values) if v is not None]
    return pd.DataFrame(rows).sort_values(["sku", "parsed_at"]).reset_index(drop=True)


def test_new_card_does_not_move_curve():
    # цены не менялись; в четвёртой точке в срез входит карточка с другим уровнем -
    # среднее скакнуло бы с 5 до 6,33, цепной индекс остаётся на месте
    prepared = _panel({"a": [5.0] * 6, "b": [5.0] * 6, "c": [None, None, None, 9.0, 9.0, 9.0]})
    curve = market_curve(prepared, "level", T)
    assert list(curve["level_value"]) == [5.0] * 6


def test_curve_follows_average_change():
    prepared = _panel({"a": [5.0, 5.1, 5.2, 5.3, 5.4, 5.5], "b": [3.0, 3.1, 3.2, 3.3, 3.4, 3.5]})
    curve = market_curve(prepared, "level", T)
    assert list(curve["level_value"].round(6)) == [4.0, 4.1, 4.2, 4.3, 4.4, 4.5]


def test_first_point_is_mean_level_of_slice():
    prepared = _panel({"a": [5.0] * 6, "b": [3.0] * 6})
    curve = market_curve(prepared, "level", T)
    assert curve["level_value"].iloc[0] == 4.0
    assert curve["parsed_at"].iloc[0] == T[0]


def test_only_ok_points_are_in_curve():
    prepared = _panel({"a": [5.0] * 6, "b": [5.0] * 6})
    curve = market_curve(prepared, "level", T[2:5])
    assert list(curve["parsed_at"]) == list(T[2:5])


def test_change_across_dropped_point_is_not_lost():
    # третья точка не в срезе: изменение карточки за неё достаётся следующей точке среза
    prepared = _panel({"a": [5.0, 5.0, 5.2, 5.4, 5.4, 5.4]})
    curve = market_curve(prepared, "level", [T[0], T[1], T[3], T[4]])
    assert list(curve["level_value"].round(6)) == [5.0, 5.0, 5.4, 5.4]


def test_empty_slice_gives_empty_curve():
    prepared = _panel({"a": [5.0] * 6})
    curve = market_curve(prepared, "level", pd.DatetimeIndex([]))
    assert curve.empty
    assert list(curve.columns) == ["parsed_at", "level_value"]