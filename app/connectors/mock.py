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


def has_copy_block(sql_text: str, table: str) -> bool:
    """Есть ли в дампе блок COPY таблицы. Пустая таблица блок тоже даёт."""
    return re.search(rf"^COPY public\.{table} \(", sql_text, re.MULTILINE) is not None


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

    # ---------- 4. Поисковая выдача ----------
    def get_search_snapshots(self, set_id: str,
                             date_from: datetime | None = None) -> pd.DataFrame:
        """Плитки выдачи сета из свежей выгрузки ch_search_*.csv. В старых дампах
        выгрузки выдачи нет - пусто, а не ошибка."""
        files = sorted(self.data_dir.glob("ch_search_*.csv"))
        if not files:
            return schema.empty(schema.SEARCH_SNAPSHOTS)
        s = pd.read_csv(files[-1], na_values=["\\N"],
                        usecols=["run_at", "set_id", "stratum_id", "sku"],
                        dtype={"set_id": str, "stratum_id": str, "sku": str})
        s = s[s["set_id"] == str(set_id)].copy()
        s["run_at"] = pd.to_datetime(s["run_at"], errors="coerce")
        if date_from is not None:
            s = s[s["run_at"] >= date_from]
        return schema.conform(s, schema.SEARCH_SNAPSHOTS)

    # ---------- 5. Ниши ----------
    @cached_property
    def _raw_niches(self) -> dict[str, pd.DataFrame] | None:
        """Таблицы ниш. None - дамп снят до миграций ниш (V7-V11), таблиц в нём нет."""
        tables = ("niche", "niche_card", "niche_candidate")
        if not all(has_copy_block(self._dump, t) for t in tables):
            return None
        return {t: parse_copy_block(self._dump, t) for t in tables}

    def get_niches(self) -> pd.DataFrame:
        raw = self._raw_niches
        if raw is None:
            return schema.empty(schema.NICHES)
        n = raw["niche"].copy()
        for ref in ("marketplace", "geo", "category"):
            titles = self._raw[ref].set_index("id")["title"]
            n[ref] = n[f"{ref}_id"].map(titles)
        return schema.conform(n.rename(columns={"external_id": "niche_id"}), schema.NICHES)

    def get_niche_cards(self, niche_id: str) -> pd.DataFrame:
        raw = self._raw_niches
        if raw is None:
            return schema.empty(schema.NICHE_CARDS)
        n = raw["niche"]
        core_ids = n.loc[n["external_id"] == niche_id, "id"]
        if core_ids.empty:
            return schema.empty(schema.NICHE_CARDS)
        core_id = core_ids.iloc[0]

        members = raw["niche_card"]
        members = members.loc[members["niche_id"] == core_id, ["card_id", "added_at"]]
        # у card своя колонка niche_id (ниша-создатель) - к составу ниши она не относится
        cards = self._raw["card"].rename(columns={"id": "card_id"}).drop(columns=["niche_id"])
        cand = raw["niche_candidate"]
        # по card_id: у подставленного размера sku кандидата - исходной карточки,
        # card_id - карточки нужного размера, которая в нише
        cand = cand.loc[cand["niche_id"] == core_id, ["card_id", "similarity"]]

        c = members.merge(cards, on="card_id", how="inner").merge(cand, on="card_id", how="left")
        c["niche_id"] = niche_id
        return schema.conform(c, schema.NICHE_CARDS)