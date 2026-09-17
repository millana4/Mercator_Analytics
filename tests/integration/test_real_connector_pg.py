# ---------- RealConnector: SQL против восстановленного дампа ядра ----------
from types import SimpleNamespace

import pytest

from app.connectors.real import RealConnector
from tests.integration.conftest import CORE_TEST_DB, TEST_PG_ADMIN_PASSWORD, TEST_PG_ADMIN_USER, TEST_PG_HOST, TEST_PG_PORT


@pytest.fixture(scope="module")
def real_connector(core_test_db):
    settings = SimpleNamespace(
        core_pg_host=TEST_PG_HOST, core_pg_port=TEST_PG_PORT,
        core_pg_db=CORE_TEST_DB, core_pg_user=TEST_PG_ADMIN_USER,
        core_pg_password=TEST_PG_ADMIN_PASSWORD,
    )
    # core_dsn - property на Settings, а не отдельное поле; RealConnector
    # обращается к settings.core_dsn напрямую внутри create_engine
    settings.core_dsn = (f"postgresql+psycopg://{TEST_PG_ADMIN_USER}:{TEST_PG_ADMIN_PASSWORD}"
                         f"@{TEST_PG_HOST}:{TEST_PG_PORT}/{CORE_TEST_DB}")
    return RealConnector(settings)


def test_get_sets_returns_canonical_columns(real_connector):
    df = real_connector.get_sets()
    assert not df.empty
    assert list(df.columns) == ["set_id", "marketplace", "category", "geo", "created_at"]


def test_get_cards_has_url_column(real_connector):
    """Регрессия на баг из шага 8 - url пропадал из SQL_CARDS."""
    set_id = real_connector.get_sets()["set_id"].iloc[0]
    df = real_connector.get_cards(set_id)
    assert "url" in df.columns
    assert df["url"].notna().any()


def test_get_subcategories_parses_attrs(real_connector):
    set_id = real_connector.get_sets()["set_id"].iloc[0]
    df = real_connector.get_subcategories(set_id)
    assert not df.empty
    assert df["gender"].notna().any()
    assert df["layer"].dtype.name == "Int64"


def test_get_strata_joins_subcategory(real_connector):
    set_id = real_connector.get_sets()["set_id"].iloc[0]
    df = real_connector.get_strata(set_id)
    assert not df.empty
    assert df["is_seasonal"].dtype == "boolean"


def test_get_cards_filtered_by_set(real_connector):
    sets = real_connector.get_sets()
    if len(sets) < 2:
        pytest.skip("В дампе меньше двух сетов - не на чем проверить фильтрацию")
    cards_a = set(real_connector.get_cards(sets["set_id"].iloc[0])["card_id"])
    cards_b = set(real_connector.get_cards(sets["set_id"].iloc[1])["card_id"])
    assert cards_a.isdisjoint(cards_b)
