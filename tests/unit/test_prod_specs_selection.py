# ---------- Рекомендации по закупкам: отбор карточек страты и отпечаток состава ----------
import pandas as pd

from app.connectors import schema
from app.prod_specs.stratum_selection import (rank_candidates, restrict_to_labeled, select_for_set,
                                              selection_hash, unlabeled_skus)

S1 = "s-1"
S2 = "s-2"
NO_RANKING = schema.empty(schema.SEARCH_RANKING)
NO_REVIEWS = pd.DataFrame({"sku": [], "reviews_count": []})


def cands(stratum: str, skus: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"stratum_id": stratum, "card_id": [f"card-{s}" for s in skus],
                         "sku": skus, "name": [f"Карточка {s}" for s in skus],
                         "url": [f"https://ozon/{s}" for s in skus]})


def ranking(rows: list[tuple]) -> pd.DataFrame:
    """Строки: (страта, sku, снимков выдачи, средняя позиция)."""
    return pd.DataFrame(rows, columns=["stratum_id", "sku", "appearances", "mean_position"])


def reviews(pairs: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame({"sku": list(pairs), "reviews_count": list(pairs.values())})


def test_rank_order_appearances_position_reviews():
    r = ranking([(S1, "1", 3, 5.0), (S1, "2", 3, 2.0), (S1, "3", 5, 9.0)])

    ranked = rank_candidates(cands(S1, ["1", "2", "3", "4", "5"]), r, reviews({"4": 100, "5": 200}))

    # больше снимков выше; при равенстве ниже позиция; без выдачи - по отзывам
    assert ranked["sku"].tolist() == ["3", "2", "1", "5", "4"]


def test_ties_broken_by_numeric_sku():
    ranked = rank_candidates(cands(S1, ["10", "9"]), NO_RANKING, NO_REVIEWS)

    assert ranked["sku"].tolist() == ["9", "10"]


def test_without_search_data_reviews_decide():
    ranked = rank_candidates(cands(S1, ["1", "2", "3"]), NO_RANKING, reviews({"2": 50, "3": 10}))

    assert ranked["sku"].tolist() == ["2", "3", "1"]    # нет отзывов - как ноль


def test_below_minimum_skipped_and_minimum_included():
    nine = [str(i) for i in range(1, 10)]
    ten = [str(i) for i in range(1, 11)]

    assert select_for_set(cands(S1, nine), NO_RANKING, NO_REVIEWS) == {}
    selected = select_for_set(cands(S1, ten), NO_RANKING, NO_REVIEWS)
    assert list(selected) == [S1]
    assert selected[S1].n_cards == 10


def test_cap_at_maximum_takes_best():
    skus = [str(i) for i in range(1, 31)]

    selected = select_for_set(cands(S1, skus), NO_RANKING, reviews({s: int(s) for s in skus}))

    assert selected[S1].skus == [str(i) for i in range(30, 10, -1)]   # лучшие 20 по отзывам
    assert selected[S1].n_cards == 20


def test_strata_are_independent():
    first = [str(i) for i in range(1, 13)]
    second = [str(i) for i in range(101, 112)]
    candidates = pd.concat([cands(S1, first), cands(S2, second)], ignore_index=True)
    r = ranking([(S1, "7", 4, 1.0)])

    selected = select_for_set(candidates, r, reviews({"111": 500}))

    assert sorted(selected) == [S1, S2]
    assert selected[S1].skus[0] == "7"
    assert selected[S2].skus[0] == "111"
    assert selected[S1].n_cards == 12
    assert selected[S2].n_cards == 11


def test_duplicate_rows_removed():
    skus = [str(i) for i in range(1, 13)]
    candidates = pd.concat([cands(S1, skus), cands(S1, skus)], ignore_index=True)

    selected = select_for_set(candidates, NO_RANKING, NO_REVIEWS)

    assert selected[S1].n_cards == 12


def test_empty_candidates():
    empty = schema.empty(schema.PROD_SPECS_CANDIDATES)

    assert select_for_set(empty, NO_RANKING, NO_REVIEWS) == {}


def test_hash_independent_of_order():
    assert selection_hash(["1", "2", "3"]) == selection_hash(["3", "1", "2"])
    assert selection_hash([1, 2]) == selection_hash(["1", "2"])
    assert len(selection_hash(["1"])) == 64


def test_hash_changes_with_composition():
    base = selection_hash(["1", "2", "3"])

    assert selection_hash(["1", "2", "4"]) != base
    assert selection_hash(["1", "2"]) != base
    assert selection_hash(["1", "2", "3", "4"]) != base


def _selection_of_four():
    return select_for_set(cands(S1, ["1", "2", "3", "4"]), NO_RANKING, NO_REVIEWS,
                          min_cards=1)[S1]


def test_unlabeled_skus():
    selection = _selection_of_four()

    assert unlabeled_skus(selection, ["card-1", "card-3"]) == ["2", "4"]
    assert unlabeled_skus(selection, []) == ["1", "2", "3", "4"]
    assert unlabeled_skus(selection, ["card-1", "card-2", "card-3", "card-4"]) == []


def test_restrict_to_labeled():
    selection = _selection_of_four()

    restricted = restrict_to_labeled(selection, ["card-1", "card-3", "card-99"])

    assert restricted.stratum_id == S1
    assert restricted.skus == ["1", "3"]
    assert restricted.cards["card_id"].tolist() == ["card-1", "card-3"]
    assert restricted.selection_hash == selection_hash(["1", "3"])
    assert restricted.selection_hash != selection.selection_hash


def test_restrict_to_labeled_none():
    assert restrict_to_labeled(_selection_of_four(), []) is None