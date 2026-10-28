# ---------- MockConnector: статус сета, квоты и «Отслеживать» (V18) и старые дампы ----------
import pandas as pd

from app.connectors import schema
from app.connectors.mock import MockConnector

TS = "2026-09-08 16:17:47+03"


def _copy(table: str, columns: list[str], rows: list[tuple]) -> str:
    lines = [f"COPY public.{table} ({', '.join(columns)}) FROM stdin;"]
    lines += ["\t".join("\\N" if v is None else str(v) for v in row) for row in rows]
    lines.append("\\.")
    return "\n".join(lines)


CARD_COLUMNS = ["id", "stratum_id", "sku", "name", "url", "collection", "status",
                "created_at", "dropped_at", "out_of_stock_since", "last_measured_at"]


def _dump(tmp_path, *, v18: bool) -> MockConnector:
    set_cols = ["id", "marketplace_id", "category_id", "geo_id", "created_at"]
    set_row = ("s0", 1, 2, 1, TS)
    sub_cols = ["id", "set_id", "title", "attrs"]
    sub_rows = [("sc1", "s0", "Верхняя одежда", '{"segment": "f", "layer": 3}'),
                ("sc2", "s0", "Нижнее белье", '{"segment": "f", "layer": 0}')]
    st_cols = ["id", "subcategory_id", "item", "query", "count", "is_seasonal", "base_share"]
    st_rows = [("st1", "sc1", "Куртка", "Куртка для девочки", 29, "f", None),
               ("st2", "sc2", "Трусы", "Трусы для девочки", 2, "f", None)]
    if v18:
        set_cols += ["status", "activated_at"]
        set_row += ("collecting", None)
        sub_cols += ["share", "tracked"]
        sub_rows = [r + (s, t) for r, s, t in zip(sub_rows, ("0.048", "0.0079"), ("t", "f"))]
        st_cols += ["analysis_quota"]
        st_rows = [r + (q,) for r, q in zip(st_rows, (7, 1))]
    blocks = [
        _copy("marketplace", ["id", "title"], [(1, "ozon")]),
        _copy("geo", ["id", "title"], [(1, "Санкт-Петербург")]),
        _copy("category", ["id", "title"], [(2, "kids_clothing")]),
        _copy("set", set_cols, [set_row]),
        _copy("subcategory", sub_cols, sub_rows),
        _copy("stratum", st_cols, st_rows),
        _copy("card", CARD_COLUMNS, []),
    ]
    (tmp_path / "pg_20261027_1200.sql").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return MockConnector(tmp_path)


def test_v18_dump_status_quota_tracked(tmp_path):
    conn = _dump(tmp_path, v18=True)
    sets = conn.get_sets()
    assert list(sets.columns) == list(schema.SETS)
    assert sets.iloc[0]["status"] == "collecting"
    assert pd.isna(sets.iloc[0]["activated_at"])
    strata = conn.get_strata("s0").set_index("item")
    assert list(conn.get_strata("s0").columns) == list(schema.STRATA)
    assert strata.loc["Куртка", "analysis_quota"] == 7
    assert bool(strata.loc["Куртка", "tracked"]) is True
    assert bool(strata.loc["Трусы", "tracked"]) is False


def test_old_dump_defaults(tmp_path):
    # дамп до V18: сеты активные с момента создания, квот нет, подкатегории не отслеживаются
    conn = _dump(tmp_path, v18=False)
    sets = conn.get_sets()
    assert sets.iloc[0]["status"] == "active"
    assert sets.iloc[0]["activated_at"] == sets.iloc[0]["created_at"]
    strata = conn.get_strata("s0")
    assert strata["analysis_quota"].isna().all()
    assert not strata["tracked"].any()