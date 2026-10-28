# ---------- Привязка замеров к карточке среза по sku (история слитых дублей V19) ----------
import pandas as pd

from app.connectors import schema
from app.etl.regularize import attach_to_cards, build_regular_panel


def _measurements(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["card_id", "sku", "parsed_at", "card_price"])
    for col, val in (("geo", "Санкт-Петербург"), ("price", None), ("original_price", None),
                     ("quantity", None), ("rating", None), ("reviews_count", None), ("parser_id", "1")):
        df[col] = val
    return schema.conform(df, schema.MEASUREMENTS)


def _cards() -> pd.DataFrame:
    return pd.DataFrame({"card_id": pd.array(["new", "gone"], dtype="string"),
                         "sku": pd.array([101, 103], dtype="Int64"),
                         "status": ["active", "dropped"]})


def test_history_under_old_card_id_goes_to_set_card():
    m = _measurements([
        ("old", 101, "2026-10-01 10:00", 1000.0),   # удалённая строка-дубль
        ("old", 101, "2026-10-02 10:00", 1010.0),
        ("new", 101, "2026-10-03 10:00", 1020.0),
        ("x", 102, "2026-10-03 10:00", 500.0),      # чужой sku
        ("gone", 103, "2026-10-03 10:00", 700.0),   # выбывшая карточка среза
    ])
    out = attach_to_cards(m, _cards())
    assert out["card_id"].tolist() == ["new", "new", "new"]
    assert out["sku"].tolist() == [101, 101, 101]


def test_panel_of_attached_history_is_one_series():
    m = _measurements([("old", 101, f"2026-10-0{d} 10:00", 1000.0) for d in range(1, 5)]
                      + [("new", 101, "2026-10-05 10:00", 1000.0)])
    panel = build_regular_panel(attach_to_cards(m, _cards()), date_to=pd.Timestamp("2026-10-05 12:37"))
    assert set(panel["card_id"]) == {"new"}
    assert panel["parsed_at"].min() == pd.Timestamp("2026-10-01 12:37")   # история с 1 октября