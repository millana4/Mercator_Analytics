# ---------- Отбор карточек ниши: близость, лимит target_count, гейт ----------
import pandas as pd

from app.analytics.selection import select_for_niche

END = pd.Timestamp("2026-08-21 00:37")   # точка расчёта: все ряды заканчиваются в ней


def _cards(rows):
    """rows: (card_id, status, similarity)."""
    df = pd.DataFrame(rows, columns=["card_id", "status", "similarity"])
    df["similarity"] = df["similarity"].astype("Float64")
    return df


def _panel(points: dict[str, int], empty_price: dict[str, int] | None = None):
    """points: card_id -> число точек сетки подряд, ряд заканчивается в END.
    empty_price: card_id -> сколько последних точек «нет в наличии»: как после
    регуляризации - цена протянута из предыдущей точки, точка негодная."""
    empty_price = empty_price or {}
    rows = []
    for card_id, n in points.items():
        for k in range(n):
            no_price = k >= n - empty_price.get(card_id, 0)
            rows.append({"card_id": card_id,
                         "parsed_at": END - pd.Timedelta(hours=12 * (n - 1 - k)),
                         "card_price": 1000.0,
                         "is_valid": not no_price})
    df = pd.DataFrame(rows)
    df["card_price"] = df["card_price"].astype("float64")
    return df


def test_takes_closest_up_to_target_count():
    ids = [f"c{i:02d}" for i in range(35)]
    cards = _cards([(cid, "active", 0.99 - i * 0.001) for i, cid in enumerate(ids)])
    sel = select_for_niche(cards, _panel({cid: 10 for cid in ids}),
                           target_count=30, min_points=5, min_cards=26)
    assert sel.sufficient
    assert sel.card_ids == ids[:30]
    assert sel.total_quota == 30


def test_equal_similarity_longer_series_first():
    cards = _cards([("short", "active", 0.95), ("long", "active", 0.95)])
    sel = select_for_niche(cards, _panel({"short": 6, "long": 12}),
                           target_count=1, min_points=5, min_cards=1)
    assert sel.card_ids == ["long"]


def test_missing_similarity_goes_last():
    cards = _cards([("no_sim", "active", None), ("with_sim", "active", 0.50)])
    sel = select_for_niche(cards, _panel({"no_sim": 20, "with_sim": 6}),
                           target_count=1, min_points=5, min_cards=1)
    assert sel.card_ids == ["with_sim"]


def test_inactive_short_and_out_of_stock_excluded():
    cards = _cards([("ok", "active", 0.90), ("dropped", "dropped", 0.99),
                    ("short", "active", 0.98), ("gaps", "active", 0.97)])
    # у gaps последние 4 точки «нет в наличии»: в точке расчёта замер без цены
    panel = _panel({"ok": 6, "dropped": 20, "short": 4, "gaps": 8}, empty_price={"gaps": 4})
    sel = select_for_niche(cards, panel, target_count=30, min_points=5, min_cards=1)
    assert sel.card_ids == ["ok"]


def test_card_outside_niche_ignored():
    # замеры другой карточки с тем же sku (например, выбывшей карточки страты)
    cards = _cards([("member", "active", 0.90)])
    sel = select_for_niche(cards, _panel({"member": 6, "foreign": 30}),
                           target_count=30, min_points=5, min_cards=1)
    assert sel.card_ids == ["member"]


def test_below_gate_insufficient():
    ids = [f"c{i:02d}" for i in range(25)]
    cards = _cards([(cid, "active", 0.9) for cid in ids])
    sel = select_for_niche(cards, _panel({cid: 10 for cid in ids}),
                           target_count=30, min_points=5, min_cards=26)
    assert not sel.sufficient
    assert sel.card_ids == []
    assert "пригодных карточек 25 при минимуме 26" in sel.insufficient_reason


def test_exactly_gate_sufficient():
    ids = [f"c{i:02d}" for i in range(26)]
    cards = _cards([(cid, "active", 0.9) for cid in ids])
    sel = select_for_niche(cards, _panel({cid: 10 for cid in ids}),
                           target_count=30, min_points=5, min_cards=26)
    assert sel.sufficient
    assert len(sel.card_ids) == 26


def test_gate_counts_only_long_enough_series():
    long_ids = [f"l{i:02d}" for i in range(20)]
    short_ids = [f"s{i:02d}" for i in range(10)]
    cards = _cards([(cid, "active", 0.9) for cid in long_ids + short_ids])
    panel = _panel({**{cid: 10 for cid in long_ids}, **{cid: 3 for cid in short_ids}})
    sel = select_for_niche(cards, panel, target_count=30, min_points=5, min_cards=26)
    assert not sel.sufficient
    assert "пригодных карточек 20" in sel.insufficient_reason