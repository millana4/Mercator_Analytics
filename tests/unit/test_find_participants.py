import pandas as pd

from app.analytics.algorithms.common import find_participants


def _cards():
    return pd.DataFrame([
        {"card_id": "11111111-1111-1111-1111-111111111111", "sku": 111,
         "name": "Товар 111", "url": "https://example.com/111"},
        {"card_id": "22222222-2222-2222-2222-222222222222", "sku": 222,
         "name": "Товар 222", "url": "https://example.com/222"},
        {"card_id": "33333333-3333-3333-3333-333333333333", "sku": 333,
         "name": "Товар 333", "url": "https://example.com/333"},
    ])


def _panel():
    t = pd.to_datetime
    return pd.DataFrame([
        {"sku": 111, "parsed_at": t("2026-08-01"), "down_flag": False, "up_flag": False},
        {"sku": 111, "parsed_at": t("2026-08-03"), "down_flag": True,  "up_flag": False},
        {"sku": 111, "parsed_at": t("2026-08-08"), "down_flag": False, "up_flag": False},
        {"sku": 222, "parsed_at": t("2026-08-01"), "down_flag": False, "up_flag": False},
        {"sku": 222, "parsed_at": t("2026-08-05"), "down_flag": True,  "up_flag": False},
        {"sku": 333, "parsed_at": t("2026-08-01"), "down_flag": False, "up_flag": False},
        {"sku": 333, "parsed_at": t("2026-08-15"), "down_flag": False, "up_flag": False},
    ])


def test_participant_inside_window_included():
    result = find_participants(_panel(), "down", pd.Timestamp("2026-08-01"),
                               pd.Timestamp("2026-08-10"), _cards())
    assert {p["sku"] for p in result} == {111, 222}


def test_never_flagged_card_excluded():
    result = find_participants(_panel(), "down", pd.Timestamp("2026-08-01"),
                               pd.Timestamp("2026-12-31"), _cards())
    assert 333 not in {p["sku"] for p in result}


def test_flag_outside_window_not_counted():
    # у 222 флаг стоит 05.08 - если окно закончится раньше, участником быть не должен
    result = find_participants(_panel(), "down", pd.Timestamp("2026-08-01"),
                               pd.Timestamp("2026-08-04"), _cards())
    assert {p["sku"] for p in result} == {111}


def test_returns_name_and_url():
    result = find_participants(_panel(), "down", pd.Timestamp("2026-08-01"),
                               pd.Timestamp("2026-08-10"), _cards())
    item = next(p for p in result if p["sku"] == 111)
    assert item["name"] == "Товар 111"
    assert item["url"] == "https://example.com/111"


def test_no_flags_empty_list():
    result = find_participants(_panel(), "up", pd.Timestamp("2026-08-01"),
                               pd.Timestamp("2026-08-10"), _cards())
    assert result == []
    