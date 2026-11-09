# ---------- RealConnector: живой Postgres ядра + ClickHouse ----------
from datetime import datetime

import clickhouse_connect
import pandas as pd
from sqlalchemy import create_engine, text

from app.config.settings import Settings
from app.connectors import schema

SQL_SETS = """
SELECT s.id AS set_id, m.title AS marketplace, c.title AS category,
       g.title AS geo, s.created_at, s.status, s.activated_at
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
       st.item, st.query, st.count, st.is_seasonal, st.base_share,
       st.analysis_quota, sc.tracked
FROM stratum st
JOIN subcategory sc ON sc.id = st.subcategory_id
WHERE sc.set_id = :set_id
"""

# Карточки сета через состав страт (V19 ядра: карточка - товар, членство - stratum_card).
# Закрытое членство - для этого сета карточка выбыла (dropped, dropped_at - закрытие),
# даже если товар снова в обходе ради другого сета или ниши.
SQL_CARDS = """
SELECT c.id AS card_id, sm.stratum_id, c.sku, c.name, c.url, c.collection,
       CASE WHEN sm.removed_at IS NOT NULL THEN 'dropped' ELSE c.status END AS status,
       c.created_at,
       CASE WHEN sm.removed_at IS NOT NULL THEN sm.removed_at ELSE c.dropped_at END AS dropped_at,
       c.out_of_stock_since, c.last_measured_at
FROM card c
JOIN stratum_card sm ON sm.card_id = c.id
JOIN stratum st ON st.id = sm.stratum_id
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

# stratum_id - страта открытого членства карточки (V19); у карточек вне страт - NULL;
# в канон id идут строкой, приведение в SQL делает это явно.
# Кандидат - по card_id: у подставленного размера sku кандидата остаётся от исходной
# карточки, а в нише - карточка нужного размера (на неё указывает card_id кандидата).
SQL_NICHE_CARDS = """
SELECT n.external_id AS niche_id, c.id AS card_id,
       (SELECT sm.stratum_id::text FROM stratum_card sm
        WHERE sm.card_id = c.id AND sm.removed_at IS NULL
        ORDER BY sm.added_at DESC LIMIT 1) AS stratum_id,
       c.sku, c.name, c.url, c.collection, c.status,
       c.created_at, c.dropped_at, c.out_of_stock_since, c.last_measured_at,
       nc.added_at, cand.similarity
FROM niche n
JOIN niche_card nc ON nc.niche_id = n.id
JOIN card c ON c.id = nc.card_id
LEFT JOIN niche_candidate cand ON cand.niche_id = n.id AND cand.card_id = c.id
WHERE n.external_id = :niche_id
"""


# Карточки, пригодные для рекомендаций по закупкам: активные члены страт сета
# (открытое членство) с адресом фото и полным набором карточки (V21 ядра).
SQL_PROD_SPECS_CANDIDATES = """
SELECT sm.stratum_id::text AS stratum_id, c.id::text AS card_id, c.sku, c.name, c.url
FROM card c
JOIN stratum_card sm ON sm.card_id = c.id AND sm.removed_at IS NULL
JOIN stratum st ON st.id = sm.stratum_id
JOIN subcategory sc ON sc.id = st.subcategory_id
WHERE sc.set_id = :set_id AND c.status = 'active'
  AND c.image_url IS NOT NULL AND c.image_url <> '' AND c.content_txt IS NOT NULL
"""

# Признаки карточек от нейросети (V22 ядра)
SQL_PROD_SPECS_CARDS = """
SELECT card_id::text AS card_id, prompt_version, attrs
FROM prod_specs_card
WHERE card_id::text = ANY(:card_ids)
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

    def get_prod_specs_candidates(self, set_id: str) -> pd.DataFrame:
        return schema.conform(self._query(SQL_PROD_SPECS_CANDIDATES, set_id=set_id),
                              schema.PROD_SPECS_CANDIDATES)

    def get_prod_specs_cards(self, card_ids: list[str]) -> pd.DataFrame:
        if not card_ids:
            return schema.empty(schema.PROD_SPECS_CARDS)
        return schema.conform(self._query(SQL_PROD_SPECS_CARDS, card_ids=list(card_ids)),
                              schema.PROD_SPECS_CARDS)

    def get_search_ranking(self, set_id: str, date_from: datetime) -> pd.DataFrame:
        # алиасы не совпадают с колонками таблицы: в ClickHouse алиас перекрывает колонку
        sql = ("SELECT toString(stratum_id) AS stratum_key, toString(sku) AS sku_key,"
               " uniqExact(run_at) AS appearances, avg(position) AS mean_position"
               " FROM search_snapshot"
               " WHERE set_id = {set_id:UUID} AND run_at >= {date_from:DateTime}"
               " GROUP BY stratum_key, sku_key")
        df = self._ch_client().query_df(
            sql, parameters={"set_id": str(set_id), "date_from": date_from})
        if df.empty:
            return schema.empty(schema.SEARCH_RANKING)
        df = df.rename(columns={"stratum_key": "stratum_id", "sku_key": "sku"})
        return schema.conform(df, schema.SEARCH_RANKING)

    def get_reviews(self, skus: list[str]) -> pd.DataFrame:
        ids = sorted({int(s) for s in skus if str(s).isdigit()})
        if not ids:
            return schema.empty(schema.REVIEWS)
        sql = ("SELECT toString(sku) AS sku_key, argMax(reviews_count, parsed_at) AS reviews_last"
               " FROM measurement WHERE sku IN {skus:Array(Int64)} GROUP BY sku_key")
        df = self._ch_client().query_df(sql, parameters={"skus": ids})
        if df.empty:
            return schema.empty(schema.REVIEWS)
        df = df.rename(columns={"sku_key": "sku", "reviews_last": "reviews_count"})
        return schema.conform(df, schema.REVIEWS)

    def _ch_client(self):
        s = self.settings
        return clickhouse_connect.get_client(
            host=s.core_ch_host, port=s.core_ch_port, username=s.core_ch_user,
            password=s.core_ch_password, database=s.core_ch_db,
        )

    def get_measurements(self, skus: list[int], date_from: datetime | None = None,
                          date_to: datetime | None = None) -> pd.DataFrame:
        client = self._ch_client()
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

    def get_search_snapshots(self, set_id: str,
                             date_from: datetime | None = None) -> pd.DataFrame:
        where = ["set_id = {set_id:UUID}"]
        params: dict = {"set_id": str(set_id)}
        if date_from is not None:
            where.append("run_at >= {date_from:DateTime}")
            params["date_from"] = date_from
        sql = ("SELECT run_at, toString(stratum_id) AS stratum_id, sku"
               f" FROM search_snapshot WHERE {' AND '.join(where)}")
        df = self._ch_client().query_df(sql, parameters=params)
        if df.empty:
            return schema.empty(schema.SEARCH_SNAPSHOTS)
        return schema.conform(df, schema.SEARCH_SNAPSHOTS)