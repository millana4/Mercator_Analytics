# ---------- MockConnector: дампы Postgres (.sql) + выгрузка ClickHouse (.csv) ----------
import json
import re
from datetime import datetime
from functools import cached_property
from pathlib import Path

import pandas as pd

from app.connectors import schema


def parse_copy_block(sql_text: str, table: str) -> pd.DataFrame:
    """Достаёт данные таблицы из блока COPY ... FROM stdin; в pg-дампе."""
    lines = sql_text.splitlines()
    start, cols = None, None
    pattern = re.compile(rf"^COPY public\.{table} \(([^)]+)\) FROM stdin;\s*$")
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            cols = [c.strip() for c in m.group(1).split(",")]
            start = i + 1
            break
    if start is None:
        raise ValueError(f"В дампе нет блока COPY для таблицы {table}")

    rows = []
    for line in lines[start:]:
        if line == "\\.":
            break
        rows.append([None if p == "\\N" else p for p in line.split("\t")])
    return pd.DataFrame(rows, columns=cols)


class MockConnector:
    """Читает выгруженные дампы. Схема на выходе - та же, что у RealConnector."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    # ---------- 1. Поиск свежих файлов ----------
    def _latest(self, pattern: str) -> Path:
        files = sorted(self.data_dir.glob(pattern))
        if not files:
            raise FileNotFoundError(f"В {self.data_dir} нет файлов по шаблону {pattern}")
        return files[-1]          # имена содержат дату, поэтому последний = свежий

    @cached_property
    def _dump(self) -> str:
        return self._latest("pg_*.sql").read_text(encoding="utf-8")

    # ---------- 2. Справочники и структура ----------
    @cached_property
    def _raw(self) -> dict[str, pd.DataFrame]:
        return {t: parse_copy_block(self._dump, t)
                for t in ("marketplace", "geo", "category", "set",
                          "subcategory", "stratum", "card")}

    def get_sets(self) -> pd.DataFrame:
        s = self._raw["set"].copy()
        for ref in ("marketplace", "geo", "category"):
            titles = self._raw[ref].set_index("id")["title"]
            s[ref] = s[f"{ref}_id"].map(titles)
        return schema.conform(s.rename(columns={"id": "set_id"}), schema.SETS)

    def get_subcategories(self, set_id: str) -> pd.DataFrame:
        sc = self._raw["subcategory"]
        sc = sc[sc["set_id"] == set_id].copy()
        attrs = sc["attrs"].apply(lambda a: json.loads(a) if a else {})
        sc["gender"] = attrs.apply(lambda a: a.get("segment"))
        sc["layer"] = attrs.apply(lambda a: a.get("layer"))
        return schema.conform(sc.rename(columns={"id": "subcategory_id"}), schema.SUBCATEGORIES)

    def get_strata(self, set_id: str) -> pd.DataFrame:
        sub = self.get_subcategories(set_id)
        st = self._raw["stratum"].rename(columns={"id": "stratum_id"})
        st = st.merge(sub.rename(columns={"title": "subcategory"}),
                      on="subcategory_id", how="inner")
        st["is_seasonal"] = st["is_seasonal"] == "t"
        return schema.conform(st, schema.STRATA)

    def get_cards(self, set_id: str) -> pd.DataFrame:
        strata_ids = set(self.get_strata(set_id)["stratum_id"])
        c = self._raw["card"].rename(columns={"id": "card_id"})
        c = c[c["stratum_id"].isin(strata_ids)]
        return schema.conform(c, schema.CARDS)

    # ---------- 3. Замеры ----------
    def get_measurements(self, skus: list[int], date_from: datetime | None = None,
                          date_to: datetime | None = None) -> pd.DataFrame:
        m = pd.read_csv(self._latest("ch_measurement_*.csv"), na_values=["\\N"])
        m["parsed_at"] = pd.to_datetime(m["parsed_at"], errors="coerce")
        m = m[m["sku"].isin(skus)]
        if date_from is not None:
            m = m[m["parsed_at"] >= date_from]
        if date_to is not None:
            m = m[m["parsed_at"] < date_to]
        return schema.conform(m, schema.MEASUREMENTS)