# ---------- Отбор карточек категории: пошаговое урезание квот ----------
import pandas as pd
import pytest

from app.analytics.selection import select_for_category
from app.assignments.models import Assignment, StratumTask

END = pd.Timestamp("2026-10-26 00:37")   # точка расчёта
POINTS = 6                               # у каждой карточки 6 годных точек - хватает окну EWMA (5)


def _task(item: str, quota: int) -> StratumTask:
    return StratumTask(gender="f", layer=1, subcategory=item, item=item, query=item,
                       quota_category=quota, plan_parsing=max(quota, 1), track_subcategory=False,
                       is_seasonal=False, base_share=None)


def _select(spec: dict[str, tuple[int, int]], missing: dict[str, int] | None = None,
            min_cards: int = 1):
    """spec: страта -> (квота, пригодных карточек); missing: страта -> квота, страты нет в базе."""
    missing = missing or {}
    tasks = [_task(item, q) for item, (q, _) in spec.items()]
    tasks += [_task(item, q) for item, q in missing.items()]
    strata = pd.DataFrame([{"stratum_id": f"s_{item}", "gender": "f", "layer": 1,
                            "subcategory": item, "item": item} for item in spec])
    cards, rows = [], []
    for item, (_, n) in spec.items():
        for i in range(n):
            card_id = f"{item}_{i}"
            cards.append({"card_id": card_id, "stratum_id": f"s_{item}", "status": "active"})
            rows += [{"card_id": card_id, "parsed_at": END - pd.Timedelta(hours=12 * k),
                      "card_price": 1000.0, "is_valid": True} for k in range(POINTS)]
    cards_df = pd.DataFrame(cards, columns=["card_id", "stratum_id", "status"])
    panel = pd.DataFrame(rows, columns=["card_id", "parsed_at", "card_price", "is_valid"])
    assignment = Assignment(category="kids_clothing", source_name="test.csv", strata=tasks)
    return select_for_category(assignment, strata, cards_df, panel,
                               min_points=5, min_cards=min_cards, as_of=END)


def test_no_cut_when_deficit_within_limit():
    sel = _select({"A": (50, 50), "B": (50, 48)})          # дефицит 2%
    assert sel.sufficient
    assert sel.scale_factor == 1.0
    assert len(sel.card_ids) == 98
    assert [(s.needed, s.taken, s.reason) for s in sel.shortfalls] == \
        [(50, 48, "недостаточно карточек")]


def test_stepwise_cut_until_deficit_allowed():
    # дефицит 5,6% - урезание шагами 0,95, 0,90, ... до 0,55, где дефицит 3 из 60 = 5%
    sel = _select({"A": (50, 50), "B": (50, 50), "C": (6, 1), "D": (1, 0)})
    assert sel.sufficient
    assert sel.scale_factor == pytest.approx(0.55)
    assert len(sel.card_ids) == 57                         # 28 + 28 + 1 + 0


def test_empty_small_stratum_does_not_zero_category():
    # пустая страта с квотой 2 при дефиците 3,2% - урезания нет, категория считается
    sel = _select({"A": (30, 30), "B": (30, 30), "C": (2, 0)})
    assert sel.sufficient
    assert sel.scale_factor == 1.0
    assert len(sel.card_ids) == 60


def test_missing_stratum_goes_to_warning_not_cut():
    sel = _select({"A": (50, 50), "B": (50, 50)}, missing={"Z": 10})
    assert sel.sufficient
    assert sel.scale_factor == 1.0
    assert len(sel.card_ids) == 100
    assert [(s.level_key, s.reason) for s in sel.shortfalls] == [("Z", "страты нет в базе")]


def test_gate_after_cut():
    sel = _select({"A": (50, 50), "B": (50, 50), "C": (6, 1), "D": (1, 0)}, min_cards=100)
    assert not sel.sufficient
    assert sel.card_ids == []
    assert "пригодных карточек 57 при минимуме 100" in sel.insufficient_reason