# ---------- Приведение неравномерных замеров к регулярной сетке ----------
import pandas as pd

from app.config.policies import MAX_MEASUREMENT_AGE_HOURS, grid_times

PRICE_COL = "card_price"


def build_grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """Точки сетки за период, не позже end. Состав точек берётся из политик.
    Точки позже end не создаются: замеров для них ещё нет, а LOCF перенёс бы
    в будущую точку последнюю цену."""
    days = pd.date_range(start.normalize(), end.normalize(), freq="D")
    points = [day + pd.Timedelta(hours=t.hour, minutes=t.minute)
              for day in days for t in grid_times()]
    return pd.DatetimeIndex(sorted(p for p in points if p <= end))


def last_grid_points(as_of, n: int) -> pd.DatetimeIndex:
    """n последних точек сетки, не позже as_of. Окно алгоритма при отборе карточек."""
    as_of = pd.Timestamp(as_of)
    days_back = n // len(grid_times()) + 2
    return build_grid(as_of - pd.Timedelta(days=days_back), as_of)[-n:]


def _regularize_card(card_meas: pd.DataFrame, grid_df: pd.DataFrame,
                     price_col: str, max_age: pd.Timedelta) -> pd.DataFrame:
    """Одна карточка на сетке.

    В точку t берётся последний замер не позже t. Точка годная (is_valid), если этот
    замер не старше max_age и в нём есть цена. Иначе точка - пробел: замера за max_age
    не было или последний замер «нет в наличии». В пробел подставляется цена
    предыдущей точки (LOCF); годна ли карточка для алгоритма, решает отбор по окну.
    До первого замера карточки точек нет."""
    card_meas = card_meas.sort_values("parsed_at")
    merged = pd.merge_asof(
        grid_df,
        card_meas[["parsed_at", price_col]].rename(columns={"parsed_at": "source_at"}),
        left_on="grid_time", right_on="source_at", direction="backward",
    )
    # замер считается наблюдённым, если он попал в интервал между соседними точками
    prev_grid = merged["grid_time"].shift(1)
    merged["is_observed"] = merged["source_at"].notna() & (
        prev_grid.isna() | (merged["source_at"] > prev_grid)
    )
    merged["is_valid"] = (merged["source_at"].notna()
                          & (merged["grid_time"] - merged["source_at"] < max_age)
                          & merged[price_col].notna())
    merged[price_col] = merged[price_col].where(merged["is_valid"]).ffill()
    # до первого замера карточки данных нет вообще - такие точки отбрасываем
    return merged[merged["source_at"].notna()]


def build_regular_panel(measurements: pd.DataFrame, *, price_col: str = PRICE_COL,
                        date_from: pd.Timestamp | None = None,
                        date_to: pd.Timestamp | None = None,
                        max_age_hours: int | None = None) -> pd.DataFrame:
    """Сырые замеры (каноническая схема) -> регулярная панель.

    На выходе: card_id, sku, parsed_at (точка сетки), card_price, is_observed, is_valid.
    date_to - точка расчёта: сетка заканчивается на ней.
    is_valid=False - пробел (нет замера за max_age_hours или последний замер без цены);
    цена в пробеле - из предыдущей точки.
    """
    columns = ["card_id", "sku", "parsed_at", price_col, "is_observed", "is_valid"]
    if measurements.empty:
        return pd.DataFrame(columns=columns)

    m = measurements.copy()
    m[price_col] = pd.to_numeric(m[price_col], errors="coerce")
    max_age = pd.Timedelta(hours=max_age_hours or MAX_MEASUREMENT_AGE_HOURS)

    start = pd.Timestamp(date_from) if date_from is not None else m["parsed_at"].min()
    end = pd.Timestamp(date_to) if date_to is not None else m["parsed_at"].max()
    grid_df = pd.DataFrame({"grid_time": build_grid(start, end)})
    if grid_df.empty:
        return pd.DataFrame(columns=columns)

    parts = []
    for (card_id, sku), group in m.groupby(["card_id", "sku"], sort=False):
        part = _regularize_card(group, grid_df, price_col, max_age)
        if part.empty:
            continue
        part = part.assign(card_id=card_id, sku=sku)
        parts.append(part)

    if not parts:
        return pd.DataFrame(columns=columns)

    panel = pd.concat(parts, ignore_index=True).rename(columns={"grid_time": "parsed_at"})
    panel = panel[columns]
    return panel.sort_values(["card_id", "parsed_at"]).reset_index(drop=True)


def attach_to_cards(measurements: pd.DataFrame, cards: pd.DataFrame) -> pd.DataFrame:
    """Замеры - к активной карточке среза по sku.

    Карточка - товар (V19 ядра): один sku - один card_id. Но у товаров, слитых из дублей
    при V19, часть истории в ClickHouse лежит под удалённым card_id. Привязка по sku
    собирает всю историю товара в ряд карточки среза; замеры чужих sku отбрасываются.
    Внутри сета и ниши sku уникален."""
    active = cards[cards["status"] == "active"].dropna(subset=["sku"])
    card_of_sku = active.drop_duplicates("sku").set_index("sku")["card_id"]
    out = measurements[measurements["sku"].isin(card_of_sku.index)].copy()
    out["card_id"] = out["sku"].map(card_of_sku).astype("string")
    return out.reset_index(drop=True)