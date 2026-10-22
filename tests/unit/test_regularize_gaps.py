# ---------- Регуляризация с допуском 24 ч и пригодность карточки по окну алгоритма ----------
import pandas as pd

from app.analytics.selection import eligible_cards
from app.etl.regularize import build_regular_panel

AS_OF = pd.Timestamp("2026-10-22 12:37")    # точка расчёта
STEP = pd.Timedelta(hours=12)
SLOTS = 20
EWMA_POINTS, MACD_POINTS = 5, 9


def _meas(skip=(), no_price=()):
    """Замеры карточки c1: слот k - за 1 ч до точки AS_OF - k*12 ч, цена 1000 + (SLOTS - k).
    skip - слоты без замера (обход не выполнился), no_price - замер «нет в наличии»."""
    rows = [{"card_id": "c1", "sku": 1,
             "parsed_at": AS_OF - k * STEP - pd.Timedelta(hours=1),
             "card_price": None if k in no_price else 1000.0 + (SLOTS - k)}
            for k in range(SLOTS) if k not in skip]
    df = pd.DataFrame(rows)
    df["card_price"] = df["card_price"].astype("float64")
    return df


def _gap(back):
    """Пропуск двух слотов даёт пробел ровно в точке AS_OF - back*12 ч."""
    return (back, back + 1)


def _point(panel, back):
    return panel[panel["parsed_at"] == AS_OF - back * STEP].iloc[0]


def _eligible(meas, min_points):
    panel = build_regular_panel(meas, date_to=AS_OF)
    cards = pd.DataFrame({"card_id": ["c1"], "status": ["active"]})
    return eligible_cards(cards, panel, min_points, as_of=AS_OF)["card_id"].tolist()


# ---------- Регуляризация ----------
def test_no_measurement_for_24h_is_gap_filled_from_previous_point():
    panel = build_regular_panel(_meas(skip=_gap(4)), date_to=AS_OF)
    gap, prev = _point(panel, 4), _point(panel, 5)
    assert not gap["is_valid"]
    assert gap["card_price"] == prev["card_price"]
    assert _point(panel, 3)["is_valid"] and _point(panel, 5)["is_valid"]


def test_out_of_stock_is_gap_filled_from_previous_point():
    panel = build_regular_panel(_meas(no_price=(3,)), date_to=AS_OF)
    gap, prev = _point(panel, 3), _point(panel, 4)
    assert not gap["is_valid"]
    assert gap["card_price"] == prev["card_price"]


def test_daily_crawl_keeps_every_point_valid():
    # замер раз в сутки (каждый второй слот) - каждая точка покрыта замером не старше 24 ч
    panel = build_regular_panel(_meas(skip=tuple(range(1, SLOTS, 2))), date_to=AS_OF)
    recent = panel[panel["parsed_at"] > AS_OF - 10 * STEP]
    assert recent["is_valid"].all()


def test_grid_stops_at_calculation_point():
    # расчёт в 00:45: точки 12:37 того же дня (будущей) в панели нет
    run_at = pd.Timestamp("2026-10-22 00:45")
    panel = build_regular_panel(_meas(), date_to=run_at)
    assert panel["parsed_at"].max() == pd.Timestamp("2026-10-22 00:37")


# ---------- Пригодность по окну алгоритма ----------
def test_gap_24h_ago_excludes_both():
    meas = _meas(skip=_gap(2))
    assert _eligible(meas, EWMA_POINTS) == []
    assert _eligible(meas, MACD_POINTS) == []


def test_gap_3_days_ago_ewma_only():
    meas = _meas(skip=_gap(6))
    assert _eligible(meas, EWMA_POINTS) == ["c1"]
    assert _eligible(meas, MACD_POINTS) == []


def test_gap_6_days_ago_both():
    meas = _meas(skip=_gap(12))
    assert _eligible(meas, EWMA_POINTS) == ["c1"]
    assert _eligible(meas, MACD_POINTS) == ["c1"]


def test_out_of_stock_at_calculation_point_excluded():
    assert _eligible(_meas(no_price=(0,)), EWMA_POINTS) == []


def test_no_fresh_measurement_at_calculation_point_excluded():
    assert _eligible(_meas(skip=_gap(0)), EWMA_POINTS) == []