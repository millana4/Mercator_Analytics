from app.config.settings import get_settings
from app.connectors.base import DataConnector
from app.connectors.mock import MockConnector
from app.connectors.real import RealConnector


def get_connector() -> DataConnector:
    s = get_settings()
    if s.connector_kind == "mock":
        return MockConnector(s.mock_data_dir)
    if s.connector_kind == "real":
        return RealConnector(s)
    raise ValueError(f"Неизвестный CONNECTOR_KIND: {s.connector_kind!r}")