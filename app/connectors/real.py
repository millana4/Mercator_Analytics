# ---------- RealConnector: живой Postgres ядра + ClickHouse ----------
from datetime import datetime

import clickhouse_connect
import pandas as pd
from sqlalchemy import create_engine, text

from app.config.settings import Settings
from app.connectors import schema

SQL_SETS = """
SELECT s.id AS set_id, m.title AS marketplace, c.title AS category,
       g.title AS geo, s.created_at
FROM set s
JOIN marketplace m ON m.id = s.marketplace_id
JOIN category c ON c.id = s.category_id
JOIN geo g ON g.id = s.geo_id
"""

SQL_SUBCATEGORIES = """
SELECT sc.id AS subcategory_id, sc.set_id, sc.title,
       sc.attrs->>'segment' AS gender,
       (sc.attrs->>'layer')::int AS layer
FROM subcategory sc
WHERE sc.set_id = :set_id
"""

SQL_STRATA = """
SELECT st.id AS stratum_id, st.subcategory_id, sc.title AS subcategory,
       sc.attrs->>'segment' AS gender, (sc.attrs->>'layer')::int AS layer,
       st.item, st.query, st.count, st.is_seasonal, st.base_share
FROM stratum st
JOIN subcategory sc ON sc.id = st.subcategory_id
WHERE sc.set_id = :set_id
"""

SQL_CARDS = """
SELECT c.id AS card_id, c.stratum_id, c.sku, c.name, c.url, c.collection, c.status,
       c.created_at, c.dropped_at, c.out_of_stock_since, c.last_measured_at
FROM card c
JOIN stratum st ON st.id = c.stratum_id
JOIN subcategory sc ON sc.id = st.subcategory_id
WHERE sc.set_id = :set_id
"""

SQL_NICHES = """
SELECT n.external_id AS niche_id, m.title AS marketplace, c.title AS category,
       g.title AS geo, n.status, n.search_query, n.error, n.target_count, n.plan_count
FROM niche n
JOIN marketplace m ON m.id = n.marketplace_id
JOIN category c ON c.id = n.category_id
JOIN geo g ON g.id = n.geo_id
"""

# stratum_id::text - у карточек, созданных для ниши, страты нет (NULL);
# в канон id идут строкой, приведение в SQL делает это явно.
# Кандидат - по card_id: у подставленного размера sku кандидата остаётся от исходной
# карточки, а в нише - карточка нужного размера (на неё указывает card_id кандидата).
SQL_NICHE_CARDS = """
SELECT n.external_id AS niche_id, c.id AS card_id, c.stratum_id::text AS stratum_id,
       c.sku, c.name, c.url, c.collection, c.status,
       c.created_at, c.dropped_at, c.out_of_stock_since, c.last_measured_at,
       nc.added_at, cand.similarity
FROM niche n
JOIN niche_card nc ON nc.niche_id = n.id
JOIN card c ON c.id = nc.card_id
LEFT JOIN niche_candidate cand ON cand.niche_id = n.id AND cand.card_id = c.id
WHERE n.external_id = :niche_id
"""


class RealConnector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = create_engine(settings.core_dsn, pool_pre_ping=True)

    def _query(self, sql: str, **params) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)

    def get_sets(self) -> pd.DataFrame:
        return schema.conform(self._query(SQL_SETS), schema.SETS)

    def get_subcategories(self, set_id: str) -> pd.DataFrame:
        return schema.conform(self._query(SQL_SUBCATEGORIES, set_id=set_id),
                              schema.SUBCATEGORIES)

    def get_strata(self, set_id: str) -> pd.DataFrame:
        return schema.conform(self._query(SQL_STRATA, set_id=set_id), schema.STRATA)

    def get_cards(self, set_id: str) -> pd.DataFrame:
        return schema.conform(self._query(SQL_CARDS, set_id=set_id), schema.CARDS)

    def get_niches(self) -> pd.DataFrame:
        return schema.conform(self._query(SQL_NICHES), schema.NICHES)

    def get_niche_cards(self, niche_id: str) -> pd.DataFrame:
        return schema.conform(self._query(SQL_NICHE_CARDS, niche_id=niche_id),
                              schema.NICHE_CARDS)

    def get_measurements(self, skus: list[int], date_from: datetime | None = None,
                          date_to: datetime | None = None) -> pd.DataFrame:
        s = self.settings
        client = clickhouse_connect.get_client(
            host=s.core_ch_host, port=s.core_ch_port, username=s.core_ch_user,
            password=s.core_ch_password, database=s.core_ch_db,
        )
        where = ["sku IN {skus:Array(Int64)}"]
        params: dict = {"skus": list(skus)}
        if date_from is not None:
            where.append("parsed_at >= {date_from:DateTime}")
            params["date_from"] = date_from
        if date_to is not None:
            where.append("parsed_at < {date_to:DateTime}")
            params["date_to"] = date_to
        sql = ("SELECT card_id, sku, parsed_at, geo, card_price, price, original_price,"
               " quantity, rating, reviews_count, parser_id"
               f" FROM measurement WHERE {' AND '.join(where)}")
        df = client.query_df(sql, parameters=params)
        return schema.conform(df, schema.MEASUREMENTS)