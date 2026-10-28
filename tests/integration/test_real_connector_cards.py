# ---------- RealConnector: карточки сета через состав страт (V19) ----------
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import psycopg
import pytest

from app.connectors.real import RealConnector
from tests.integration.conftest import (CORE_TEST_DB, TEST_PG_ADMIN_PASSWORD,
                                        TEST_PG_ADMIN_USER, TEST_PG_HOST, TEST_PG_PORT)

CORE_DSN = (f"host={TEST_PG_HOST} port={TEST_PG_PORT} user={TEST_PG_ADMIN_USER} "
            f"password={TEST_PG_ADMIN_PASSWORD} dbname={CORE_TEST_DB}")
URL = "https://www.ozon.ru/product/hudi-1/"


@pytest.fixture(scope="module")
def real_connector(core_test_db):
    settings = SimpleNamespace(
        core_dsn=(f"postgresql+psycopg://{TEST_PG_ADMIN_USER}:{TEST_PG_ADMIN_PASSWORD}"
                  f"@{TEST_PG_HOST}:{TEST_PG_PORT}/{CORE_TEST_DB}"))
    return RealConnector(settings)


@pytest.fixture
def members(core_test_db):
    """Две карточки в страте сета: с открытым членством и с закрытым (товар активен)."""
    d = SimpleNamespace(open=uuid4(), closed=uuid4())
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.stratum_card')")
        if cur.fetchone()[0] is None:
            pytest.skip("Дамп ядра снят до V19 - состава страт нет")
        cur.execute("""
            SELECT s.id, s.marketplace_id, st.id FROM stratum st
            JOIN subcategory sc ON sc.id = st.subcategory_id
            JOIN set s ON s.id = sc.set_id
            LIMIT 1""")
        d.set_id, mp, d.stratum_id = cur.fetchone()
        card_sql = ("INSERT INTO card (id, marketplace_id, sku, name, url, status) "
                    "VALUES (%s, %s, %s, %s, %s, 'active')")
        cur.execute(card_sql, (d.open, mp, "9000000201", "Открытое членство", URL))
        cur.execute(card_sql, (d.closed, mp, "9000000202", "Закрытое членство", URL))
        cur.execute("INSERT INTO stratum_card (stratum_id, card_id) VALUES (%s, %s)",
                    (d.stratum_id, d.open))
        cur.execute("INSERT INTO stratum_card (stratum_id, card_id, removed_at) "
                    "VALUES (%s, %s, '2026-10-20 10:00:00+03')", (d.stratum_id, d.closed))
    yield d
    # удаление карточек убирает и членство (каскад)
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM card WHERE id = ANY(%s)", ([d.open, d.closed],))


def test_cards_of_set_status_by_membership(real_connector, members):
    cards = real_connector.get_cards(str(members.set_id)).set_index("card_id")
    opened = cards.loc[str(members.open)]
    closed = cards.loc[str(members.closed)]
    assert opened["status"] == "active"
    assert opened["stratum_id"] == str(members.stratum_id)
    # членство закрыто - для этого сета карточка выбыла, хотя товар активен
    assert closed["status"] == "dropped"
    assert closed["dropped_at"] == pd.Timestamp("2026-10-20 10:00:00")