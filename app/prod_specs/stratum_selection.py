# ---------- Рекомендации по закупкам: отбор карточек страты и отпечаток состава ----------
# Чистые функции без обращений к базам: на входе датафреймы коннектора
# (PROD_SPECS_CANDIDATES, SEARCH_RANKING, REVIEWS), на выходе - отбор по стратам.
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from app.config import policies


@dataclass(frozen=True, eq=False)
class Selection:
    """Отобранные карточки одной страты по порядку (лучшие первыми).
    cards - колонки кандидатов плюс appearances, mean_position, reviews_count."""
    stratum_id: str
    cards: pd.DataFrame
    skus: list[str]
    selection_hash: str

    @property
    def n_cards(self) -> int:
        return len(self.skus)


def selection_hash(skus: Iterable) -> str:
    """Отпечаток состава отобранных sku: не зависит от порядка."""
    joined = ",".join(sorted(str(s) for s in skus))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def rank_candidates(candidates: pd.DataFrame, ranking: pd.DataFrame,
                    reviews: pd.DataFrame) -> pd.DataFrame:
    """Подходящие карточки в порядке отбора внутри каждой страты: больше снимков выдачи,
    ниже средняя позиция, больше отзывов, меньше sku (как число). Нет данных о выдаче
    или отзывах - нули (у взрослой одежды выдачи нет: порядок задают отзывы)."""
    c = candidates.drop_duplicates(["stratum_id", "card_id"]).copy()
    c["stratum_id"] = c["stratum_id"].astype("string")
    c["sku"] = c["sku"].astype("string")

    r = ranking[["stratum_id", "sku", "appearances", "mean_position"]].copy()
    r["stratum_id"] = r["stratum_id"].astype("string")
    r["sku"] = r["sku"].astype("string")
    rv = reviews[["sku", "reviews_count"]].copy()
    rv["sku"] = rv["sku"].astype("string")

    c = c.merge(r, on=["stratum_id", "sku"], how="left").merge(rv, on="sku", how="left")
    c["appearances"] = pd.to_numeric(c["appearances"], errors="coerce").fillna(0)
    c["reviews_count"] = pd.to_numeric(c["reviews_count"], errors="coerce").fillna(0)
    c["mean_position"] = pd.to_numeric(c["mean_position"], errors="coerce")
    c["_sku_num"] = pd.to_numeric(c["sku"], errors="coerce")
    c = c.sort_values(["stratum_id", "appearances", "mean_position", "reviews_count", "_sku_num"],
                      ascending=[True, False, True, False, True],
                      na_position="last", kind="mergesort")
    return c.drop(columns="_sku_num").reset_index(drop=True)


def select_for_set(candidates: pd.DataFrame, ranking: pd.DataFrame, reviews: pd.DataFrame,
                   min_cards: int | None = None, max_cards: int | None = None) -> dict[str, Selection]:
    """Отбор по стратам сета: страта с числом подходящих карточек меньше min_cards не
    анализируется (в результат не попадает); иначе берутся первые max_cards по порядку.
    Ключ результата - stratum_id."""
    min_cards = policies.PROD_SPECS_MIN_CARDS if min_cards is None else min_cards
    max_cards = policies.PROD_SPECS_MAX_CARDS if max_cards is None else max_cards
    ranked = rank_candidates(candidates, ranking, reviews)

    out: dict[str, Selection] = {}
    for stratum_id, group in ranked.groupby("stratum_id", sort=True):
        if len(group) < min_cards:
            continue
        top = group.head(max_cards).reset_index(drop=True)
        skus = top["sku"].astype(str).tolist()
        out[str(stratum_id)] = Selection(str(stratum_id), top, skus, selection_hash(skus))
    return out


def unlabeled_skus(selection: Selection, labeled_card_ids: Iterable) -> list[str]:
    """sku отобранных карточек без признаков (шаг 1 нужен только им), в порядке отбора."""
    labeled = {str(c) for c in labeled_card_ids}
    cards = selection.cards
    return cards.loc[~cards["card_id"].astype(str).isin(labeled), "sku"].astype(str).tolist()


def restrict_to_labeled(selection: Selection, labeled_card_ids: Iterable) -> Selection | None:
    """Отбор только из карточек с признаками, с пересчитанным отпечатком; таких нет - None."""
    labeled = {str(c) for c in labeled_card_ids}
    cards = selection.cards[selection.cards["card_id"].astype(str).isin(labeled)].reset_index(drop=True)
    if cards.empty:
        return None
    skus = cards["sku"].astype(str).tolist()
    return Selection(selection.stratum_id, cards, skus, selection_hash(skus))