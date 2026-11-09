# ---------- RealConnector: рекомендации по закупкам (кандидаты и признаки карточек) ----------
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from app.connectors.real import RealConnector
from tests.integration.conftest import (CORE_TEST_DB, TEST_PG_ADMIN_PASSWORD,
                                        TEST_PG_ADMIN_USER, TEST_PG_HOST, TEST_PG_PORT)

CORE_DSN = (f"host={TEST_PG_HOST} port={TEST_PG_PORT} user={TEST_PG_ADMIN_USER} "
            f"password={TEST_PG_ADMIN_PASSWORD} dbname={CORE_TEST_DB}")


@pytest.fixture(scope="module")
def real_connector(core_test_db):
    settings = SimpleNamespace(
        core_dsn=(f"postgresql+psycopg://{TEST_PG_ADMIN_USER}:{TEST_PG_ADMIN_PASSWORD}"
                  f"@{TEST_PG_HOST}:{TEST_PG_PORT}/{CORE_TEST_DB}"))
    return RealConnector(settings)


@pytest.fixture
def prod_specs_cards_in_stratum(core_test_db):
    """Три карточки в страте сета: пригодная, без фото и с закрытым членством."""
    d = SimpleNamespace(ok=uuid4(), no_image=uuid4(), closed=uuid4())
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'card' AND column_name = 'image_url'")
        if cur.fetchone() is None:
            pytest.skip("Дамп ядра снят до V21 - адресов фото нет")
        cur.execute("""
            SELECT s.id, s.marketplace_id, st.id FROM stratum st
            JOIN subcategory sc ON sc.id = st.subcategory_id
            JOIN set s ON s.id = sc.set_id
            LIMIT 1""")
        d.set_id, mp, d.stratum_id = cur.fetchone()
        sql = ("INSERT INTO card (id, marketplace_id, sku, name, url, status, image_url, content_txt) "
               "VALUES (%s, %s, %s, %s, %s, 'active', %s, %s::jsonb)")
        url = "https://www.ozon.ru/product/x/"
        content = '{"name": "x"}'
        cur.execute(sql, (d.ok, mp, "9000000301", "Пригодная", url, "https://img/1.jpg", content))
        cur.execute(sql, (d.no_image, mp, "9000000302", "Без фото", url, None, content))
        cur.execute(sql, (d.closed, mp, "9000000303", "Закрытое членство", url, "https://img/3.jpg", content))
        cur.execute("INSERT INTO stratum_card (stratum_id, card_id) VALUES (%s, %s), (%s, %s)",
                    (d.stratum_id, d.ok, d.stratum_id, d.no_image))
        cur.execute("INSERT INTO stratum_card (stratum_id, card_id, removed_at) "
                    "VALUES (%s, %s, '2026-10-20 10:00:00+03')", (d.stratum_id, d.closed))
    yield d
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM card WHERE id = ANY(%s)", ([d.ok, d.no_image, d.closed],))


@pytest.fixture
def prod_specs_table(core_test_db):
    """Таблица prod_specs_card (V22 ядра): в дампе, снятом до наката ядра, её нет."""
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.prod_specs_card')")
        created = cur.fetchone()[0] is None
        if created:
            cur.execute("""CREATE TABLE prod_specs_card (
                card_id uuid PRIMARY KEY, input_hash text NOT NULL, prompt_version text NOT NULL,
                model text NOT NULL, attrs jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now())""")
    yield
    if created:
        with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE prod_specs_card")


def test_candidates_exclude_no_photo_and_closed_membership(real_connector, prod_specs_cards_in_stratum):
    d = prod_specs_cards_in_stratum

    cards = real_connector.get_prod_specs_candidates(str(d.set_id)).set_index("card_id")

    assert str(d.ok) in cards.index
    assert cards.loc[str(d.ok), "stratum_id"] == str(d.stratum_id)
    assert cards.loc[str(d.ok), "sku"] == "9000000301"
    assert cards.loc[str(d.ok), "name"] == "Пригодная"
    assert str(d.no_image) not in cards.index
    assert str(d.closed) not in cards.index


def test_prod_specs_cards_read_attrs(real_connector, prod_specs_table):
    card = uuid4()
    attrs = {"decor": ["рюши"], "extra": [{"name": "бант", "value": "есть"}]}
    with psycopg.connect(CORE_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO prod_specs_card (card_id, input_hash, prompt_version, model, attrs) "
                    "VALUES (%s, 'h', 'v1', 'm', %s::jsonb)", (card, json_text(attrs)))

    result = real_connector.get_prod_specs_cards([str(card), str(uuid4())])

    assert len(result) == 1
    assert result.iloc[0]["card_id"] == str(card)
    assert result.iloc[0]["prompt_version"] == "v1"
    assert result.iloc[0]["attrs"] == attrs


def test_prod_specs_cards_empty_list(real_connector):
    assert real_connector.get_prod_specs_cards([]).empty


def json_text(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)