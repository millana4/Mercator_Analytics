# ---------- Приведение неравномерных замеров к регулярной сетке ----------
import numpy as np
import pandas as pd

from app.config.policies import grid_times

PRICE_COL = "card_price"


def build_grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """Точки сетки за период. Состав точек берётся из политик."""
    days = pd.date_range(start.normalize(), end.normalize(), freq="D")
    points = [day + pd.Timedelta(hours=t.hour, minutes=t.minute)
              for day in days for t in grid_times()]
    return pd.DatetimeIndex(sorted(points))


def _regularize_card(card_meas: pd.DataFrame, grid_df: pd.DataFrame,
                     price_col: str) -> pd.DataFrame:
    """LOCF по одной карточке: в точку сетки переносится последний известный замер.
    Если последний замер был без цены (товара нет в наличии) - точка остаётся пустой."""
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
    # до первого замера карточки данных нет вообще - такие точки отбрасываем
    return merged[merged["source_at"].notna()]


def build_regular_panel(measurements: pd.DataFrame, *, price_col: str = PRICE_COL,
                        date_from: pd.Timestamp | None = None,
                        date_to: pd.Timestamp | None = None) -> pd.DataFrame:
    """Сырые замеры (каноническая схема) -> регулярная панель.

    На выходе: card_id, sku, parsed_at (точка сетки), card_price, is_observed.
    Пустая цена означает, что товара не было в наличии на момент последнего замера.
    """
    if measurements.empty:
        return pd.DataFrame(columns=["card_id", "sku", "parsed_at", price_col, "is_observed"])

    m = measurements.copy()
    m[price_col] = pd.to_numeric(m[price_col], errors="coerce")

    start = date_from if date_from is not None else m["parsed_at"].min()
    end = date_to if date_to is not None else m["parsed_at"].max()
    grid_df = pd.DataFrame({"grid_time": build_grid(start, end)})

    parts = []
    for (card_id, sku), group in m.groupby(["card_id", "sku"], sort=False):
        part = _regularize_card(group, grid_df, price_col)
        if part.empty:
            continue
        part = part.assign(card_id=card_id, sku=sku)
        parts.append(part)

    if not parts:
        return pd.DataFrame(columns=["card_id", "sku", "parsed_at", price_col, "is_observed"])

    panel = pd.concat(parts, ignore_index=True).rename(columns={"grid_time": "parsed_at"})
    panel = panel[["card_id", "sku", "parsed_at", price_col, "is_observed"]]
    return panel.sort_values(["card_id", "parsed_at"]).reset_index(drop=True)