# ---------- RealConnector: ниши и состав ниш против дампа ядра с V7-V11 ----------
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import psycopg
import pytest

from app.connectors import schema
from app.connectors.real import RealConnector
from tests.integration.conftest import (CORE_TEST_DB, TEST_PG_ADMIN_PASSWORD,
                                        TEST_PG_ADMIN_USER, TEST_PG_HOST, TEST_PG_PORT)

CORE_DSN = (f"host={TEST_PG_HOST} port={TEST_PG_PORT} user={TEST_PG_ADMIN_USER} "
            f"password={TEST_PG_ADMIN_PASSWORD} dbname={CORE_TEST_DB}")
# sku тестовых карточек - с префиксом 90000, как у тестовых карточек ниш на стенде
SKUS = {1: "9000000101", 2: "9000000102", 3: "9000000103"}
URL = "https://www.ozon.ru/product/platye-1/"


@pytest.fixture(scope="module")
def real_connector(core_test_db):
    # тот же способ, что в test_real_connector_pg.py: core_dsn - property на Settings
    settings = SimpleNamespace(
        core_dsn=(f"postgresql+psycopg://{TEST_PG_ADMIN_USER}:{TEST_PG_ADMIN_PASSWORD}"
                  f"@{TEST_PG_HOST}:{TEST_PG_PORT}/{CORE_TEST_DB}"))
    return RealConnector(settings)


@pytest.fixture(scope="module")
def niche_data(core_test_db):
    """Ниши A и B с составом и кандидатами, ниша C упала в ядре.
    c1 - карточка страты (в A), c2 создана для B (в A и B), c3 создана для A."""
    d = SimpleNamespace(niche_a=uuid4(), niche_b=uuid4(), niche_c=uuid4(),
                        ext_a=uuid4(), ext_b=uuid4(), ext_c=uuid4(),
                        c1=uuid4(), c2=uuid4(), c3=uuid4())
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.niche')")
        if cur.fetchone()[0] is None:
            pytest.skip("Дамп ядра снят до миграций ниш (V7-V11) - таблиц ниш нет")
        cur.execute("""
            SELECT s.marketplace_id, s.category_id, s.geo_id, st.id, m.title, c.title, g.title
            FROM stratum st
            JOIN subcategory sc ON sc.id = st.subcategory_id
            JOIN set s ON s.id = sc.set_id
            JOIN marketplace m ON m.id = s.marketplace_id
            JOIN category c ON c.id = s.category_id
            JOIN geo g ON g.id = s.geo_id
            LIMIT 1""")
        row = cur.fetchone()
        if row is None:
            pytest.skip("В дампе ядра нет страт")
        mp, cat, geo, d.stratum_id, *titles = row
        d.titles = tuple(titles)

        niche_sql = ("INSERT INTO niche (id, marketplace_id, category_id, geo_id, external_id, "
                     "description, search_query, target_count, plan_count, status, error) "
                     "VALUES (%s, %s, %s, %s, %s, 'тестовая ниша', %s, 30, 40, %s, %s)")
        cur.execute(niche_sql, (d.niche_a, mp, cat, geo, d.ext_a,
                                "платье для девочки 7 лет", "active", None))
        cur.execute(niche_sql, (d.niche_b, mp, cat, geo, d.ext_b,
                                "сарафан для девочки 5 лет", "active", None))
        cur.execute(niche_sql, (d.niche_c, mp, cat, geo, d.ext_c,
                                None, "failed", "нейросеть не ответила"))

        card_sql = ("INSERT INTO card (id, stratum_id, niche_id, sku, name, url, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'active')")
        cur.execute(card_sql, (d.c1, d.stratum_id, None, SKUS[1], "Карточка страты", URL))
        cur.execute(card_sql, (d.c2, None, d.niche_b, SKUS[2], "Создана для B", URL))
        cur.execute(card_sql, (d.c3, None, d.niche_a, SKUS[3], "Создана для A", URL))

        cur.executemany("INSERT INTO niche_card (niche_id, card_id) VALUES (%s, %s)",
                        [(d.niche_a, d.c1), (d.niche_a, d.c2), (d.niche_a, d.c3),
                         (d.niche_b, d.c2)])
        cur.executemany(
            "INSERT INTO niche_candidate (niche_id, sku, source, card_id, similarity, status) "
            "VALUES (%s, %s, %s, %s, %s, 'selected')",
            [(d.niche_a, SKUS[1], "stratum", d.c1, 0.95),
             (d.niche_a, SKUS[2], "search", d.c2, 0.9),
             (d.niche_b, SKUS[2], "search", d.c2, 0.8)])
    yield d
    # удаление ниш каскадом убирает состав, кандидатов и карточки, созданные для ниш
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM niche WHERE id = ANY(%s)", ([d.niche_a, d.niche_b, d.niche_c],))
        cur.execute("DELETE FROM card WHERE id = %s", (d.c1,))


def test_get_niches(real_connector, niche_data):
    d = niche_data
    niches = real_connector.get_niches()
    assert list(niches.columns) == list(schema.NICHES)
    by_id = niches.set_index("niche_id")
    a = by_id.loc[str(d.ext_a)]
    assert (a["marketplace"], a["category"], a["geo"]) == d.titles
    assert a["search_query"] == "платье для девочки 7 лет"
    assert a["status"] == "active"
    assert a["target_count"] == 30 and a["plan_count"] == 40
    c = by_id.loc[str(d.ext_c)]
    assert c["status"] == "failed"
    assert c["error"] == "нейросеть не ответила"
    assert pd.isna(c["search_query"])


def test_get_niche_cards_members_similarity_and_types(real_connector, niche_data):
    d = niche_data
    cards = real_connector.get_niche_cards(str(d.ext_a))
    assert list(cards.columns) == list(schema.NICHE_CARDS)
    assert cards["card_id"].dtype == "string"
    assert cards["stratum_id"].dtype == "string"
    assert cards["sku"].dtype == "Int64"
    assert cards["similarity"].dtype == "Float64"

    by_id = cards.set_index("card_id")
    assert set(by_id.index) == {str(d.c1), str(d.c2), str(d.c3)}
    assert (cards["niche_id"] == str(d.ext_a)).all()
    assert by_id.loc[str(d.c1), "similarity"] == 0.95
    assert by_id.loc[str(d.c2), "similarity"] == 0.9         # кандидат своей ниши, не 0.8 из B
    assert pd.isna(by_id.loc[str(d.c3), "similarity"])       # кандидата нет
    assert by_id.loc[str(d.c1), "stratum_id"] == str(d.stratum_id)
    assert pd.isna(by_id.loc[str(d.c2), "stratum_id"])       # создана для ниши - страты нет
    assert by_id.loc[str(d.c3), "sku"] == int(SKUS[3])


def test_card_created_for_other_niche_belongs_by_membership(real_connector, niche_data):
    d = niche_data
    cards = real_connector.get_niche_cards(str(d.ext_b))
    assert cards["card_id"].tolist() == [str(d.c2)]
    assert cards["niche_id"].iloc[0] == str(d.ext_b)
    assert cards["similarity"].iloc[0] == 0.8


def test_unknown_niche_empty(real_connector, niche_data):
    cards = real_connector.get_niche_cards(str(uuid4()))
    assert cards.empty
    assert list(cards.columns) == list(schema.NICHE_CARDS)