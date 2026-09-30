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

    def get_niches(self) -> pd.DataFrame:
        """Ниши ядра во всех статусах. niche_id = external_id ядра = id ниши аналитики."""
        ...

    def get_niche_cards(self, niche_id: str) -> pd.DataFrame:
        """Текущий состав ниши (niche_card) с близостью к референсу."""
        ...