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