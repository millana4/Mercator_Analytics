# ---------- Отбор карточек подкатегории: все годные, гейт ----------
import pandas as pd

from app.analytics.selection import select_for_subcategory

END = pd.Timestamp("2026-08-21 00:37")   # точка расчёта: все ряды заканчиваются в ней
KEY = ("f", 1, "Блузы и рубашки")        # (гендер, слой, название подкатегории)


def _strata():
    return pd.DataFrame([
        {"stratum_id": "s1", "gender": "f", "layer": 1, "subcategory": "Блузы и рубашки", "item": "Блузка"},
        {"stratum_id": "s2", "gender": "f", "layer": 1, "subcategory": "Блузы и рубашки", "item": "Рубашка"},
        {"stratum_id": "s3", "gender": "m", "layer": 1, "subcategory": "Рубашки", "item": "Рубашка"},
    ])


def _cards(rows):
    """rows: (card_id, status, stratum_id)."""
    return pd.DataFrame(rows, columns=["card_id", "status", "stratum_id"])


def _panel(points: dict[str, int]):
    """points: card_id -> число точек сетки подряд, ряд заканчивается в END."""
    rows = [{"card_id": card_id, "parsed_at": END - pd.Timedelta(hours=12 * (n - 1 - k)),
             "card_price": 1000.0, "is_valid": True}
            for card_id, n in points.items() for k in range(n)]
    return pd.DataFrame(rows)


def _own(n: int):
    """n карточек подкатегории KEY: страты s1 и s2 по очереди."""
    return [(f"c{i:02d}", "active", "s1" if i % 2 == 0 else "s2") for i in range(n)]


def test_takes_all_eligible_without_upper_limit():
    own = _own(45)
    sel = select_for_subcategory(KEY, _strata(), _cards(own), _panel({cid: 10 for cid, _, _ in own}),
                                 min_points=5, min_cards=40)
    assert sel.sufficient
    assert len(sel.card_ids) == 45        # больше min_cards: берутся все годные
    assert sel.total_quota == 40


def test_other_subcategory_ignored():
    own, foreign = _own(40), [(f"m{i:02d}", "active", "s3") for i in range(10)]
    panel = _panel({cid: 10 for cid, _, _ in own + foreign})
    sel = select_for_subcategory(KEY, _strata(), _cards(own + foreign), panel, min_points=5, min_cards=40)
    assert sorted(sel.card_ids) == sorted(cid for cid, _, _ in own)


def test_inactive_and_short_excluded():
    own = _own(40)
    extra = [("dropped", "dropped", "s1"), ("short", "active", "s2")]
    panel = _panel({**{cid: 10 for cid, _, _ in own}, "dropped": 20, "short": 4})
    sel = select_for_subcategory(KEY, _strata(), _cards(own + extra), panel, min_points=5, min_cards=40)
    assert len(sel.card_ids) == 40
    assert "dropped" not in sel.card_ids and "short" not in sel.card_ids


def test_below_gate_insufficient():
    own = _own(39)
    sel = select_for_subcategory(KEY, _strata(), _cards(own), _panel({cid: 10 for cid, _, _ in own}),
                                 min_points=5, min_cards=40)
    assert not sel.sufficient
    assert sel.card_ids == []
    assert "пригодных карточек 39 при минимуме 40" in sel.insufficient_reason


def test_exactly_gate_sufficient():
    own = _own(40)
    sel = select_for_subcategory(KEY, _strata(), _cards(own), _panel({cid: 10 for cid, _, _ in own}),
                                 min_points=5, min_cards=40)
    assert sel.sufficient
    assert len(sel.card_ids) == 40