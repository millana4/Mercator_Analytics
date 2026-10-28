# ---------- MockConnector: дамп после V19 (карточка - товар, состав страт - stratum_card) ----------
import pandas as pd

from app.connectors import schema
from app.connectors.mock import MockConnector

TS = "2026-10-27 12:00:00+03"
URL = "https://www.ozon.ru/product/hudi-1/"
NICHE_CORE = "10000000-0000-0000-0000-00000000000a"
NICHE_EXT = "e0000000-0000-0000-0000-00000000000a"


def _copy(table: str, columns: list[str], rows: list[tuple]) -> str:
    lines = [f"COPY public.{table} ({', '.join(columns)}) FROM stdin;"]
    lines += ["\t".join("\\N" if v is None else str(v) for v in row) for row in rows]
    lines.append("\\.")
    return "\n".join(lines)


CARD_COLUMNS = ["id", "marketplace_id", "niche_id", "sku", "name", "url", "collection", "status",
                "created_at", "dropped_at", "out_of_stock_since", "last_measured_at"]


def _connector(tmp_path) -> MockConnector:
    """Летний сет s0 (страта st1) и зимний s1 (страта st2).
    c1 - только лето; c2 - общий товар (лето и зима); c3 - выбыл из лета (членство
    закрыто), но снова в обходе ради ниши."""
    blocks = [
        _copy("marketplace", ["id", "title"], [(1, "ozon")]),
        _copy("geo", ["id", "title"], [(1, "Санкт-Петербург")]),
        _copy("category", ["id", "title"], [(2, "kids_clothing")]),
        _copy("set", ["id", "marketplace_id", "category_id", "geo_id", "created_at", "status",
                      "activated_at"],
              [("s0", 1, 2, 1, TS, "active", TS), ("s1", 1, 2, 1, TS, "collecting", None)]),
        _copy("subcategory", ["id", "set_id", "title", "attrs", "share", "tracked"],
              [("sc0", "s0", "Толстовки", '{"segment": "f", "layer": 2}', "0.038", "t"),
               ("sc1", "s1", "Толстовки", '{"segment": "f", "layer": 2}', "0.040", "t")]),
        _copy("stratum", ["id", "subcategory_id", "item", "query", "count", "is_seasonal",
                          "base_share", "analysis_quota"],
              [("st1", "sc0", "Худи", "Худи для девочки", 5, "f", None, 2),
               ("st2", "sc1", "Худи", "Худи для девочки", 5, "f", None, 2)]),
        _copy("card", CARD_COLUMNS,
              [("c1", 1, None, 101, "Только лето", URL, None, "active", TS, None, None, TS),
               ("c2", 1, None, 102, "Общий", URL, None, "active", TS, None, None, TS),
               ("c3", 1, None, 103, "Выбыл из лета", URL, None, "active", TS, None, None, TS)]),
        _copy("stratum_card", ["stratum_id", "card_id", "added_at", "removed_at"],
              [("st1", "c1", TS, None), ("st1", "c2", TS, None), ("st2", "c2", TS, None),
               ("st1", "c3", TS, "2026-10-20 10:00:00+03")]),
        _copy("niche", ["id", "marketplace_id", "category_id", "geo_id", "external_id",
                        "search_query", "status", "error", "target_count", "plan_count"],
              [(NICHE_CORE, 1, 2, 1, NICHE_EXT, "худи для девочки", "active", None, 30, 34)]),
        _copy("niche_card", ["niche_id", "card_id", "added_at"],
              [(NICHE_CORE, "c2", TS), (NICHE_CORE, "c3", TS)]),
        _copy("niche_candidate", ["niche_id", "sku", "card_id", "similarity", "status"],
              [(NICHE_CORE, 102, "c2", 0.9, "selected"), (NICHE_CORE, 103, "c3", 0.8, "selected")]),
    ]
    (tmp_path / "pg_20261027_1200.sql").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return MockConnector(tmp_path)


def test_cards_of_set_by_membership(tmp_path):
    conn = _connector(tmp_path)
    summer = conn.get_cards("s0").set_index("card_id")
    assert list(conn.get_cards("s0").columns) == list(schema.CARDS)
    assert set(summer.index) == {"c1", "c2", "c3"}
    assert (summer.loc[["c1", "c2"], "stratum_id"] == "st1").all()
    # членство закрыто - для летнего сета карточка выбыла, хотя товар активен
    assert summer.loc["c3", "status"] == "dropped"
    assert summer.loc["c3", "dropped_at"] == pd.Timestamp("2026-10-20 10:00:00")
    assert summer.loc["c2", "status"] == "active"

    winter = conn.get_cards("s1")
    assert winter["card_id"].tolist() == ["c2"]                 # общий товар - тот же card_id
    assert winter["stratum_id"].tolist() == ["st2"]


def test_niche_cards_stratum_from_open_membership(tmp_path):
    cards = _connector(tmp_path).get_niche_cards(NICHE_EXT).set_index("card_id")
    assert list(_connector(tmp_path).get_niche_cards(NICHE_EXT).columns) == list(schema.NICHE_CARDS)
    assert cards.loc["c2", "stratum_id"] in {"st1", "st2"}       # открытое членство
    assert pd.isna(cards.loc["c3", "stratum_id"])               # членство закрыто
    assert cards.loc["c3", "status"] == "active"                # в нише - активен