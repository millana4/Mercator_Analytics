# ---------- Активный сет связки и склейка ряда выдачи по сменам сезонного сета ----------
from datetime import datetime

import pandas as pd

from app.analytics.active_sets import active_ids_from_sets, windows_from_sets

COMBO = ("kids_clothing", "Санкт-Петербург", "ozon")


def _sets() -> pd.DataFrame:
    return pd.DataFrame({
        "set_id": ["summer", "winter", "spring", "other"],
        "marketplace": ["ozon", "ozon", "ozon", "ozon"],
        "category": ["kids_clothing", "kids_clothing", "kids_clothing", "clothing"],
        "geo": ["Санкт-Петербург"] * 4,
        "status": ["retired", "active", "collecting", "active"],
        "activated_at": pd.to_datetime(["2026-09-08 16:17", "2026-11-02 01:15", None,
                                        "2026-07-10 12:55"]),
    })


def test_only_active_set_is_shown():
    assert active_ids_from_sets(_sets(), *COMBO) == ["winter"]


def test_windows_glue_retired_and_active():
    assert windows_from_sets(_sets(), *COMBO) == [
        ("summer", datetime(2026, 9, 8, 16, 17), datetime(2026, 11, 2, 1, 15)),
        ("winter", datetime(2026, 11, 2, 1, 15), None),
    ]


def test_single_active_set_has_open_window():
    sets = _sets()
    sets = sets[sets["set_id"].isin(["other"])]
    assert windows_from_sets(sets, "clothing", "Санкт-Петербург", "ozon") == [
        ("other", datetime(2026, 7, 10, 12, 55), None)]