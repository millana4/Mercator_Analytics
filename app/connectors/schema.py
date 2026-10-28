# ---------- Каноническая схема: имена колонок и типы, одинаковые у mock и real ----------
import pandas as pd

SETS: dict[str, str] = {
    "set_id": "string",
    "marketplace": "string",
    "category": "string",
    "geo": "string",
    "created_at": "datetime64[ns]",
    "status": "string",  # draft | collecting | active | retired (V18 ядра)
    "activated_at": "datetime64[ns]",  # когда сет стал активным; склейка ряда выдачи
}

SUBCATEGORIES: dict[str, str] = {
    "subcategory_id": "string",
    "set_id": "string",
    "title": "string",
    "gender": "string",      # из attrs->>'segment' базы ядра
    "layer": "Int64",        # из attrs->>'layer'
}

STRATA: dict[str, str] = {
    "stratum_id": "string",
    "subcategory_id": "string",
    "subcategory": "string",
    "gender": "string",
    "layer": "Int64",
    "item": "string",
    "query": "string",
    "count": "Int64",        # план парсинга (stratum.count)
    "is_seasonal": "boolean",
    "base_share": "Float64",
    "analysis_quota": "Int64",  # квота анализа категории (V18 ядра; в старых дампах пусто)
    "tracked": "boolean",  # «Отслеживать подкатегорию» (subcategory.tracked)
}

CARDS: dict[str, str] = {
    "card_id": "string",
    "stratum_id": "string",
    "sku": "Int64",
    "name": "string",
    "url": "string",
    "collection": "string",
    "status": "string",
    "created_at": "datetime64[ns]",
    "dropped_at": "datetime64[ns]",
    "out_of_stock_since": "datetime64[ns]",
    "last_measured_at": "datetime64[ns]",
}

MEASUREMENTS: dict[str, str] = {
    "card_id": "string",
    "sku": "Int64",
    "parsed_at": "datetime64[ns]",
    "geo": "string",
    "card_price": "Float64",
    "price": "Float64",
    "original_price": "Float64",
    "quantity": "Int64",
    "rating": "Float64",
    "reviews_count": "Int64",
    "parser_id": "string",
}

# Плитка поисковой выдачи (search_snapshot ClickHouse): только поля для повторяемости.
SEARCH_SNAPSHOTS: dict[str, str] = {
    "run_at": "datetime64[ns]",      # плановое время прогона выдачи
    "stratum_id": "string",
    "sku": "string",
}

# Ниша: niche_id = niche.external_id ядра = niche.id аналитики.
# status - статус обработки в ядре (new ... active, failed, deleted).
NICHES: dict[str, str] = {
    "niche_id": "string",
    "marketplace": "string",
    "category": "string",
    "geo": "string",
    "status": "string",
    "search_query": "string",
    "error": "string",
    "target_count": "Int64",
    "plan_count": "Int64",
}

# Состав ниши: колонки карточки как в CARDS + принадлежность и близость.
# stratum_id пуст у карточек, созданных для ниши.
NICHE_CARDS: dict[str, str] = {
    "niche_id": "string",
    **CARDS,
    "added_at": "datetime64[ns]",     # niche_card.added_at
    "similarity": "Float64",  # niche_candidate.similarity, пусто если кандидата нет
}


def conform(df: pd.DataFrame, spec: dict[str, str]) -> pd.DataFrame:
    """Приводит датафрейм к канонической схеме: порядок колонок и типы.
    Лишние колонки отбрасываются, недостающие - ошибка."""
    missing = set(spec) - set(df.columns)
    if missing:
        raise ValueError(f"В источнике нет колонок: {sorted(missing)}")
    out = df[list(spec)].copy()
    for col, dtype in spec.items():
        if dtype.startswith("datetime"):
            out[col] = to_naive_local(out[col])
        else:
            out[col] = out[col].astype(dtype)
    return out.reset_index(drop=True)


def empty(spec: dict[str, str]) -> pd.DataFrame:
    """Пустой датафрейм канонической схемы (например, в дампе нет таблиц ниш)."""
    return conform(pd.DataFrame(columns=list(spec)), spec)

# Время в проекте локальное (сетка 00:37/12:37 задана в нём же).
# Дампы Postgres приходят со смещением +03:00, ClickHouse и CSV - без него.
# Канон: наивное локальное время, datetime64[ns].
LOCAL_TZ = "Europe/Moscow"


def to_naive_local(values: pd.Series) -> pd.Series:
    out = pd.to_datetime(values, errors="coerce")
    if isinstance(out.dtype, pd.DatetimeTZDtype):
        out = out.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return out.astype("datetime64[ns]")