# ---------- Контракт источника данных ----------
from datetime import datetime
from typing import Protocol

import pandas as pd


class DataConnector(Protocol):
    """Единый интерфейс доступа к данным сбора.
    Реализации возвращают датафреймы строго по app.connectors.schema."""

    def get_sets(self) -> pd.DataFrame: ...

    def get_subcategories(self, set_id: str) -> pd.DataFrame: ...

    def get_strata(self, set_id: str) -> pd.DataFrame: ...

    def get_cards(self, set_id: str) -> pd.DataFrame: ...

    def get_measurements(
        self,
        skus: list[int],
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> pd.DataFrame: ...

    def get_search_snapshots(self, set_id: str,
                             date_from: datetime | None = None) -> pd.DataFrame:
        """Плитки поисковой выдачи сета (схема SEARCH_SNAPSHOTS); снимков нет - пусто."""
        ...

    def get_prod_specs_candidates(self, set_id: str) -> pd.DataFrame:
        """Карточки сета, пригодные для рекомендаций по закупкам (схема PROD_SPECS_CANDIDATES):
        активные члены страт с открытой связью, с адресом фото и полным набором."""
        ...

    def get_search_ranking(self, set_id: str, date_from: datetime) -> pd.DataFrame:
        """Карточки в выдаче страт сета с date_from (схема SEARCH_RANKING); снимков нет - пусто."""
        ...

    def get_reviews(self, skus: list[str]) -> pd.DataFrame:
        """Число отзывов по последнему замеру (схема REVIEWS)."""
        ...

    def get_prod_specs_cards(self, card_ids: list[str]) -> pd.DataFrame:
        """Признаки карточек от нейросети (схема PROD_SPECS_CARDS); таблицы в источнике нет - пусто."""
        ...

    def get_niches(self) -> pd.DataFrame:
        """Ниши ядра во всех статусах. niche_id = external_id ядра = id ниши аналитики."""
        ...

    def get_niche_cards(self, niche_id: str) -> pd.DataFrame:
        """Текущий состав ниши (niche_card) с близостью к референсу."""
        ...