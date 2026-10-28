# ---------- Расчёт сета без карточек (сет в сборе до первого подбора) ----------
import pandas as pd

from app.analytics.service import run_for_set
from app.connectors import schema


class EmptySetConnector:
    """Сет в сборе: страты есть, карточек ещё нет; замеры запрашивать нельзя."""
    def get_strata(self, set_id):
        return schema.empty(schema.STRATA)

    def get_cards(self, set_id):
        return schema.empty(schema.CARDS)

    def get_measurements(self, skus):
        raise AssertionError("замеры не запрашиваются, когда у сета нет карточек")


def test_set_without_cards_is_skipped():
    row = pd.Series({"set_id": "s-new", "category": "kids_clothing",
                     "marketplace": "ozon", "geo": "Санкт-Петербург"})
    assert run_for_set(EmptySetConnector(), None, row) == []