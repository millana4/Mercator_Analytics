# ---------- build_analysis_panel: карточки без страты не теряются ----------
import pandas as pd

from app.analytics.algorithms.common import build_analysis_panel
from app.connectors import schema

START = pd.Timestamp("2026-08-01 00:37")


def _panel(card_skus):
    """card_skus: (card_id, sku); по 3 точки на карточку."""
    rows = [{"card_id": cid, "sku": sku, "parsed_at": START + pd.Timedelta(hours=12 * k),
             "card_price": 1000.0 + k, "is_observed": True}
            for cid, sku in card_skus for k in range(3)]
    return pd.DataFrame(rows)


def _cards(rows):
    """rows: (card_id, sku, stratum_id)."""
    df = pd.DataFrame(rows, columns=["card_id", "sku", "stratum_id"])
    return df.astype({"card_id": "string", "stratum_id": "string"})


def _strata():
    df = pd.DataFrame([{"stratum_id": "s1", "gender": "f", "layer": 1,
                        "item": "Платье", "subcategory": "Платья"}])
    return df.astype({"stratum_id": "string"})


def test_niche_cards_kept_with_empty_strata():
    cards = _cards([("n1", 1, None), ("n2", 2, None)])
    df = build_analysis_panel(_panel([("n1", 1), ("n2", 2)]), cards,
                              schema.empty(schema.STRATA), ["n1", "n2"])
    assert set(df["card_id"]) == {"n1", "n2"}
    assert len(df) == 6
    assert df["item"].isna().all()
    assert df["log_price"].notna().all()


def test_mixed_cards_dims_only_for_stratum_card():
    cards = _cards([("s_card", 1, "s1"), ("n_card", 2, None)])
    df = build_analysis_panel(_panel([("s_card", 1), ("n_card", 2)]), cards,
                              _strata(), ["s_card", "n_card"])
    assert len(df) == 6
    items = df.groupby("card_id")["item"].first()
    assert items["s_card"] == "Платье"
    assert pd.isna(items["n_card"])


def test_only_selected_cards():
    cards = _cards([("a", 1, "s1"), ("b", 2, "s1")])
    df = build_analysis_panel(_panel([("a", 1), ("b", 2)]), cards, _strata(), ["a"])
    assert set(df["card_id"]) == {"a"}