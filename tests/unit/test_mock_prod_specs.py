# ---------- MockConnector: рекомендации по закупкам (кандидаты, выдача, отзывы, признаки) ----------
import json

import pandas as pd

from app.connectors import schema
from app.connectors.mock import MockConnector, unescape_copy

SET_ID = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_SET = "aaaaaaaa-0000-0000-0000-000000000002"
SUB1 = "bbbbbbbb-0000-0000-0000-000000000001"
SUB2 = "bbbbbbbb-0000-0000-0000-000000000002"
S1 = "cccccccc-0000-0000-0000-000000000001"
S2 = "cccccccc-0000-0000-0000-000000000002"
S_OTHER = "cccccccc-0000-0000-0000-000000000003"


def card_id(n: int) -> str:
    return f"dddddddd-0000-0000-0000-{n:012d}"


def copy_block(table: str, columns: list[str], rows: list[list]) -> str:
    lines = [f"COPY public.{table} ({', '.join(columns)}) FROM stdin;"]
    lines += ["\t".join("\\N" if v is None else str(v) for v in row) for row in rows]
    lines.append("\\.")
    return "\n".join(lines) + "\n\n"


def make_dump(with_photo_columns: bool = True, extra: str = "") -> str:
    attrs = '{"segment": "f", "layer": 1}'
    cards = [  # id, sku, name, status, image_url, content_txt
        (1, "111", "Худи 1", "active", "https://img/1.jpg", '{"name": "Худи 1"}'),
        (2, "222", "Худи 2", "active", "https://img/2.jpg", '{"name": "Худи 2"}'),
        (3, "333", "Без фото", "active", None, '{"name": "x"}'),
        (4, "444", "Без текста", "active", "https://img/4.jpg", None),
        (5, "555", "Выбыла", "dropped", "https://img/5.jpg", '{"name": "x"}'),
        (6, "666", "Связь закрыта", "active", "https://img/6.jpg", '{"name": "x"}'),
        (7, "777", "Чужой сет", "active", "https://img/7.jpg", '{"name": "x"}'),
    ]
    card_cols = ["id", "sku", "name", "url", "status"]
    if with_photo_columns:
        card_cols += ["image_url", "content_txt"]
    card_rows = []
    for n, sku, name, status, image, content in cards:
        row = [card_id(n), sku, name, f"https://ozon/{sku}", status]
        if with_photo_columns:
            row += [image, content]
        card_rows.append(row)
    membership = [(S1, 1, None), (S2, 2, None), (S1, 3, None), (S1, 4, None), (S1, 5, None),
                  (S1, 6, "2026-10-20 10:00:00+03"), (S_OTHER, 7, None)]
    set_cols = ["id", "marketplace_id", "category_id", "geo_id", "created_at", "status", "activated_at"]
    stratum_cols = ["id", "subcategory_id", "item", "query", "count", "is_seasonal", "base_share"]
    return (
        copy_block("marketplace", ["id", "title"], [[1, "ozon"]])
        + copy_block("geo", ["id", "title"], [[1, "Санкт-Петербург"]])
        + copy_block("category", ["id", "title"], [[1, "kids_clothing"]])
        + copy_block("set", set_cols,
                     [[sid, 1, 1, 1, "2026-10-01 10:00:00+03", "active", "2026-10-01 10:00:00+03"]
                      for sid in (SET_ID, OTHER_SET)])
        + copy_block("subcategory", ["id", "set_id", "title", "attrs"],
                     [[SUB1, SET_ID, "Худи", attrs], [SUB2, OTHER_SET, "Брюки", attrs]])
        + copy_block("stratum", stratum_cols,
                     [[S1, SUB1, "Худи", "худи", 10, "f", 0.5], [S2, SUB1, "Свитшот", "свитшот", 10, "f", 0.5],
                      [S_OTHER, SUB2, "Брюки", "брюки", 10, "f", 0.5]])
        + copy_block("card", card_cols, card_rows)
        + copy_block("stratum_card", ["stratum_id", "card_id", "removed_at"],
                     [[s, card_id(n), removed] for s, n, removed in membership])
        + extra
    )


def connector(tmp_path, dump: str) -> MockConnector:
    (tmp_path / "pg_20261106_1458.sql").write_text(dump, encoding="utf-8")
    return MockConnector(tmp_path)


def test_unescape_copy():
    assert unescape_copy("a\\\\b") == "a\\b"
    assert unescape_copy("строка\\nдве\\tтаб") == "строка\nдве\tтаб"
    assert unescape_copy("без экранирования") == "без экранирования"


def test_candidates_only_active_open_with_photo_and_content(tmp_path):
    c = connector(tmp_path, make_dump()).get_prod_specs_candidates(SET_ID)

    assert list(c.columns) == list(schema.PROD_SPECS_CANDIDATES)
    # без фото, без текста, выбывшая, закрытая связь и карточка чужого сета не подходят
    assert dict(zip(c["sku"], c["stratum_id"])) == {"111": S1, "222": S2}
    assert c.set_index("sku").loc["111", "card_id"] == card_id(1)
    assert c.set_index("sku").loc["111", "url"] == "https://ozon/111"


def test_candidates_empty_for_dump_before_v21(tmp_path):
    c = connector(tmp_path, make_dump(with_photo_columns=False)).get_prod_specs_candidates(SET_ID)

    assert c.empty
    assert list(c.columns) == list(schema.PROD_SPECS_CANDIDATES)


SEARCH_HEADER = '"run_at","set_id","stratum_id","sku","position"\n'


def test_search_ranking_counts_days_and_mean_position(tmp_path):
    rows = ""
    for day, position in (("20", 1), ("21", 3), ("22", 5)):
        rows += f'"2026-10-{day} 18:37:00","{SET_ID}","{S1}","111",{position}\n'
    rows += f'"2026-10-21 18:37:00","{SET_ID}","{S1}","222",10\n'
    (tmp_path / "ch_search_20261106_1458.csv").write_text(SEARCH_HEADER + rows, encoding="utf-8")

    r = MockConnector(tmp_path).get_search_ranking(SET_ID, pd.Timestamp("2026-10-01"))

    assert list(r.columns) == list(schema.SEARCH_RANKING)
    g = r.set_index(["stratum_id", "sku"])
    assert g.loc[(S1, "111"), "appearances"] == 3
    assert g.loc[(S1, "111"), "mean_position"] == 3.0
    assert g.loc[(S1, "222"), "appearances"] == 1
    assert g.loc[(S1, "222"), "mean_position"] == 10.0


def test_search_ranking_date_from_and_set_filter(tmp_path):
    rows = (f'"2026-10-20 18:37:00","{SET_ID}","{S1}","111",1\n'
            f'"2026-10-22 18:37:00","{SET_ID}","{S1}","111",3\n'
            f'"2026-10-23 18:37:00","{SET_ID}","{S1}","111",5\n'
            f'"2026-10-23 18:37:00","{OTHER_SET}","{S_OTHER}","777",2\n')
    (tmp_path / "ch_search_20261106_1458.csv").write_text(SEARCH_HEADER + rows, encoding="utf-8")

    r = MockConnector(tmp_path).get_search_ranking(SET_ID, pd.Timestamp("2026-10-21"))

    assert len(r) == 1
    assert r.iloc[0]["appearances"] == 2
    assert r.iloc[0]["mean_position"] == 4.0


def test_search_ranking_without_dump_is_empty(tmp_path):
    r = MockConnector(tmp_path).get_search_ranking(SET_ID, pd.Timestamp("2026-10-01"))

    assert r.empty
    assert list(r.columns) == list(schema.SEARCH_RANKING)


def test_reviews_take_latest_measurement(tmp_path):
    rows = ('"card_id","sku","parsed_at","reviews_count"\n'
            f'"{card_id(1)}","111","2026-10-20 10:00:00",10\n'
            f'"{card_id(1)}","111","2026-10-22 10:00:00",15\n'
            f'"{card_id(2)}","222","2026-10-21 10:00:00",7\n'
            f'"{card_id(3)}","333","2026-10-21 10:00:00",99\n')
    (tmp_path / "ch_measurement_20261106_1458.csv").write_text(rows, encoding="utf-8")

    r = MockConnector(tmp_path).get_reviews(["111", "222"])

    assert list(r.columns) == list(schema.REVIEWS)
    assert dict(zip(r["sku"], r["reviews_count"])) == {"111": 15, "222": 7}


def test_prod_specs_cards_decode_json(tmp_path):
    attrs = {"decor": ["рюши"], "note": 'с "кавычкой"\nи строкой'}
    escaped = json.dumps(attrs, ensure_ascii=False).replace("\\", "\\\\")
    block = copy_block("prod_specs_card",
                       ["card_id", "input_hash", "prompt_version", "model", "attrs", "created_at", "updated_at"],
                       [[card_id(1), "h", "v1", "m", escaped, "2026-11-06 10:00:00+03", "2026-11-06 10:00:00+03"],
                        [card_id(2), "h", "v1", "m", "{}", "2026-11-06 10:00:00+03", "2026-11-06 10:00:00+03"]])

    r = connector(tmp_path, make_dump(extra=block)).get_prod_specs_cards([card_id(1)])

    assert list(r.columns) == list(schema.PROD_SPECS_CARDS)
    assert len(r) == 1
    assert r.iloc[0]["card_id"] == card_id(1)
    assert r.iloc[0]["prompt_version"] == "v1"
    assert r.iloc[0]["attrs"] == attrs


def write_cards_file(tmp_path, rows, name="prod_specs_card_20261109.jsonl"):
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_prod_specs_cards_from_file_when_dump_has_no_table(tmp_path):
    write_cards_file(tmp_path, [
        {"card_id": card_id(1), "sku": "111", "prompt_version": "v2", "attrs": {"decor": ["рюши"]}},
        {"card_id": card_id(2), "sku": "222", "prompt_version": "v2", "attrs": {"decor": []}}])

    r = connector(tmp_path, make_dump()).get_prod_specs_cards([card_id(1), card_id(99)])

    assert list(r.columns) == list(schema.PROD_SPECS_CARDS)
    assert len(r) == 1
    assert r.iloc[0]["card_id"] == card_id(1)
    assert r.iloc[0]["prompt_version"] == "v2"
    assert r.iloc[0]["attrs"] == {"decor": ["рюши"]}


def test_prod_specs_cards_table_in_dump_wins_over_file(tmp_path):
    write_cards_file(tmp_path, [
        {"card_id": card_id(1), "sku": "111", "prompt_version": "v2", "attrs": {"from": "file"}}])
    block = copy_block("prod_specs_card",
                       ["card_id", "input_hash", "prompt_version", "model", "attrs", "created_at", "updated_at"],
                       [[card_id(1), "h", "v1", "m", '{"from": "dump"}',
                         "2026-11-06 10:00:00+03", "2026-11-06 10:00:00+03"]])

    r = connector(tmp_path, make_dump(extra=block)).get_prod_specs_cards([card_id(1)])

    assert r.iloc[0]["attrs"] == {"from": "dump"}


def test_prod_specs_cards_without_table_is_empty(tmp_path):
    r = connector(tmp_path, make_dump()).get_prod_specs_cards([card_id(1)])

    assert r.empty
    assert list(r.columns) == list(schema.PROD_SPECS_CARDS)