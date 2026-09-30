# ---------- MockConnector: таблицы ниш из pg-дампа ----------
import pandas as pd

from app.connectors import schema
from app.connectors.mock import MockConnector

# id ниш в ядре и их external_id (= id ниши в аналитике)
CORE_A = "10000000-0000-0000-0000-00000000000a"
CORE_B = "10000000-0000-0000-0000-00000000000b"
CORE_C = "10000000-0000-0000-0000-00000000000c"
EXT_A = "e0000000-0000-0000-0000-00000000000a"
EXT_B = "e0000000-0000-0000-0000-00000000000b"
EXT_C = "e0000000-0000-0000-0000-00000000000c"
TS = "2026-09-30 10:00:00+03"
URL = "https://www.ozon.ru/product/platye-1/"

CARD_COLUMNS = ["id", "stratum_id", "niche_id", "sku", "name", "url", "collection",
                "status", "created_at", "dropped_at", "out_of_stock_since", "last_measured_at"]
CARD_ROWS = [
    ("c1", "st1", None, 101, "Карточка страты", URL, None, "active", TS, None, None, TS),
    ("c2", None, CORE_B, 102, "Создана для B", URL, None, "active", TS, None, None, TS),
    ("c3", None, CORE_A, 103, "Создана для A", URL, None, "active", TS, None, None, TS),
    ("c4", "st1", None, 104, "Не в нишах", URL, None, "active", TS, None, None, TS),
]


def _copy(table: str, columns: list[str], rows: list[tuple]) -> str:
    """Блок COPY в формате pg_dump: значения через табуляцию, NULL = \\N."""
    lines = [f"COPY public.{table} ({', '.join(columns)}) FROM stdin;"]
    lines += ["\t".join("\\N" if v is None else str(v) for v in row) for row in rows]
    lines.append("\\.")
    return "\n".join(lines)


def _base_tables(card_columns, card_rows) -> list[str]:
    return [
        _copy("marketplace", ["id", "title"], [(1, "ozon")]),
        _copy("geo", ["id", "title"], [(1, "Санкт-Петербург")]),
        _copy("category", ["id", "title"], [(2, "kids_clothing")]),
        _copy("set", ["id", "marketplace_id", "category_id", "geo_id", "created_at"],
              [("s0", 1, 2, 1, TS)]),
        _copy("subcategory", ["id", "set_id", "title", "attrs"],
              [("sc1", "s0", "Платья", '{"segment": "f", "layer": 1}')]),
        _copy("stratum", ["id", "subcategory_id", "item", "query", "count",
                          "is_seasonal", "base_share"],
              [("st1", "sc1", "Платье", "платье для девочки", 10, "f", None)]),
        _copy("card", card_columns, card_rows),
    ]


def _niche_tables() -> list[str]:
    return [
        _copy("niche", ["id", "marketplace_id", "category_id", "geo_id", "external_id",
                        "search_query", "status", "error", "target_count", "plan_count"],
              [(CORE_A, 1, 2, 1, EXT_A, "платье для девочки 7 лет", "active", None, 30, 40),
               (CORE_B, 1, 2, 1, EXT_B, "сарафан для девочки 5 лет", "active", None, 30, 40),
               (CORE_C, 1, 2, 1, EXT_C, None, "failed", "нейросеть не ответила", 30, 40)]),
        _copy("niche_card", ["niche_id", "card_id", "added_at"],
              [(CORE_A, "c1", TS), (CORE_A, "c2", TS), (CORE_A, "c3", TS), (CORE_B, "c2", TS)]),
        _copy("niche_candidate", ["niche_id", "sku", "card_id", "similarity", "status"],
              [(CORE_A, 101, "c1", 0.95, "selected"), (CORE_A, 102, "c2", 0.9, "selected"),
               (CORE_B, 102, "c2", 0.8, "selected")]),
    ]


def _connector(tmp_path, blocks: list[str]) -> MockConnector:
    (tmp_path / "pg_20260930_1200.sql").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return MockConnector(tmp_path)


def _new_dump(tmp_path) -> MockConnector:
    return _connector(tmp_path, _base_tables(CARD_COLUMNS, CARD_ROWS) + _niche_tables())


def test_get_niches(tmp_path):
    niches = _new_dump(tmp_path).get_niches()
    assert list(niches.columns) == list(schema.NICHES)
    by_id = niches.set_index("niche_id")
    assert set(by_id.index) == {EXT_A, EXT_B, EXT_C}
    a = by_id.loc[EXT_A]
    assert (a["marketplace"], a["category"], a["geo"]) == ("ozon", "kids_clothing",
                                                           "Санкт-Петербург")
    assert a["search_query"] == "платье для девочки 7 лет"
    assert a["target_count"] == 30
    c = by_id.loc[EXT_C]
    assert c["status"] == "failed"
    assert c["error"] == "нейросеть не ответила"
    assert pd.isna(c["search_query"])


def test_get_niche_cards_members_and_similarity(tmp_path):
    cards = _new_dump(tmp_path).get_niche_cards(EXT_A).set_index("card_id")
    assert set(cards.index) == {"c1", "c2", "c3"}          # c4 ни в одной нише
    assert (cards["niche_id"] == EXT_A).all()
    assert cards.loc["c1", "similarity"] == 0.95
    assert cards.loc["c2", "similarity"] == 0.9             # кандидат своей ниши, не 0.8 из B
    assert pd.isna(cards.loc["c3", "similarity"])           # кандидата нет
    assert cards.loc["c1", "stratum_id"] == "st1"
    assert pd.isna(cards.loc["c2", "stratum_id"])


def test_card_created_for_other_niche_belongs_by_membership(tmp_path):
    cards = _new_dump(tmp_path).get_niche_cards(EXT_B)
    assert cards["card_id"].tolist() == ["c2"]
    assert cards["niche_id"].iloc[0] == EXT_B
    assert cards["similarity"].iloc[0] == 0.8


def test_unknown_niche_empty(tmp_path):
    cards = _new_dump(tmp_path).get_niche_cards("ffffffff-0000-0000-0000-000000000000")
    assert cards.empty
    assert list(cards.columns) == list(schema.NICHE_CARDS)


def test_old_dump_without_niche_tables(tmp_path):
    # дамп до миграций V7-V11: таблиц ниш нет, у card нет колонки niche_id
    old_columns = [c for c in CARD_COLUMNS if c != "niche_id"]
    old_rows = [r[:2] + r[3:] for r in CARD_ROWS if r[1] is not None]
    conn = _connector(tmp_path, _base_tables(old_columns, old_rows))
    niches = conn.get_niches()
    cards = conn.get_niche_cards(EXT_A)
    assert niches.empty and list(niches.columns) == list(schema.NICHES)
    assert cards.empty and list(cards.columns) == list(schema.NICHE_CARDS)
    assert conn.get_sets()["set_id"].tolist() == ["s0"]     # старые методы работают