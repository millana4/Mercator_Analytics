# ---------- run_for_niche: расчёт ниши на подменном коннекторе ----------
import pandas as pd

from app.analytics.service import run_for_niche
from app.config import policies
from app.connectors import schema

NICHE_ID = "aaaaaaaa-0000-0000-0000-000000000001"
START = pd.Timestamp("2026-08-01 00:37")
N_POINTS = 40   # длиннее порога самого требовательного алгоритма (CUSUM - 31 точка)


def _card_id(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


def _niche_row(search_query="платье для девочки"):
    return pd.Series({"niche_id": NICHE_ID, "marketplace": "ozon", "category": "kids_clothing",
                      "geo": "Санкт-Петербург", "status": "active", "search_query": search_query,
                      "error": None, "target_count": 30, "plan_count": 40})


def _cards(n: int, status: str = "active") -> pd.DataFrame:
    """Карточки, созданные для ниши: страты нет, близость убывает с номером."""
    rows = [{"niche_id": NICHE_ID, "card_id": _card_id(i), "stratum_id": None,
             "sku": 1000 + i, "name": f"Товар {i}", "url": f"https://example.com/{i}",
             "collection": None, "status": status, "created_at": "2026-08-01",
             "dropped_at": None, "out_of_stock_since": None, "last_measured_at": None,
             "added_at": "2026-08-01", "similarity": 0.99 - i * 0.001}
            for i in range(n)]
    return schema.conform(pd.DataFrame(rows), schema.NICHE_CARDS)


def _measurements(n: int) -> pd.DataFrame:
    rows = [{"card_id": _card_id(i), "sku": 1000 + i,
             "parsed_at": START + pd.Timedelta(hours=12 * k), "geo": "Санкт-Петербург",
             # колебание 0,1-0,2%: далеко от порогов событий, но ряд не постоянный
             "card_price": 1000.0 * (1 + 0.001 * ((i + k) % 3)),
             "price": None, "original_price": None, "quantity": 5,
             "rating": 4.9, "reviews_count": 100, "parser_id": "test"}
            for i in range(n) for k in range(N_POINTS)]
    return schema.conform(pd.DataFrame(rows), schema.MEASUREMENTS)


class FakeConnector:
    """Отдаёт заранее собранный состав ниши и замеры, запоминает запросы замеров."""

    def __init__(self, cards: pd.DataFrame, measurements: pd.DataFrame):
        self.cards = cards
        self.measurements = measurements
        self.measurement_calls: list[list[int]] = []

    def get_niche_cards(self, niche_id: str) -> pd.DataFrame:
        assert niche_id == NICHE_ID
        return self.cards

    def get_measurements(self, skus, date_from=None, date_to=None) -> pd.DataFrame:
        self.measurement_calls.append(list(skus))
        m = self.measurements
        return m[m["sku"].isin(skus)].reset_index(drop=True)


def test_niche_cards_without_strata_are_calculated():
    results = run_for_niche(FakeConnector(_cards(35), _measurements(35)), _niche_row())
    assert [r.algorithm for r in results] == ["EWMA", "MACD", "CUSUM"]
    for r in results:
        assert r.sufficient, r.insufficient_reason
        assert (r.level, r.set_id, r.level_key) == ("niche", NICHE_ID, NICHE_ID)
        assert r.category == "kids_clothing"
        assert r.n_cards == 30        # target_count; остальные 5 - запас на ротацию


def test_too_few_cards_insufficient_for_all_algorithms():
    n = policies.MIN_CARDS_NICHE - 1
    results = run_for_niche(FakeConnector(_cards(n), _measurements(n)), _niche_row())
    assert len(results) == 3
    for r in results:
        assert not r.sufficient
        assert f"пригодных карточек {n}" in r.insufficient_reason
        assert "«платье для девочки»" in r.warning


def test_no_active_cards_skips_measurements():
    conn = FakeConnector(_cards(30, status="dropped"), _measurements(30))
    results = run_for_niche(conn, _niche_row())
    assert conn.measurement_calls == []
    assert not any(r.sufficient for r in results)
    assert {r.insufficient_reason for r in results} == {"нет замеров по активным карточкам ниши"}


def test_empty_niche_without_query():
    # только что созданная ниша: состава нет, запрос нейросеть ещё не составила
    conn = FakeConnector(schema.empty(schema.NICHE_CARDS), schema.empty(schema.MEASUREMENTS))
    results = run_for_niche(conn, _niche_row(search_query=None))
    assert len(results) == 3
    assert not any(r.sufficient for r in results)
    assert all("аналитики ниши:" in r.warning for r in results)