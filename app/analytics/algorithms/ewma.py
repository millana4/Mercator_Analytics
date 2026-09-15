# ---------- Алгоритм 1: производные по каскаду EWMA (основной детектор) ----------
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.algorithms.common import detect_events, drop_short_series, ewm_smooth
from app.config.policies import GRID_STEPS_PER_DAY

ALGORITHM = "EWMA"


# ---------- Настройки алгоритма ----------
@dataclass(frozen=True)
class EwmaParams:
    span_level_1: int = 3           # первое сглаживание, шагов сетки
    span_level_2: int = 5           # второе сглаживание, поверх первого
    window_days: int = 3            # окно накопленного изменения
    card_threshold: float = 0.1202  # порог карточки за окно, лог-единицы (~12.8%)
    market_threshold: float = 0.08  # порог доли участвующих
    min_points: int = 5             # каскаду хватает пяти точек
    min_cards: int = 25             # минимум карточек в срезе
    n_bootstrap: int = 1000
    ci_alpha: float = 0.05
    random_seed: int = 42

    @property
    def window_steps(self) -> int:
        return self.window_days * GRID_STEPS_PER_DAY


PARAMS = EwmaParams()


def prepare(df: pd.DataFrame, p: EwmaParams = PARAMS) -> pd.DataFrame:
    df = drop_short_series(df, p.min_points)
    no_price = df["log_price"].isna()

    df["level_smooth_1"] = ewm_smooth(df, "log_price", p.span_level_1)
    df.loc[no_price, "level_smooth_1"] = np.nan

    df["level_smooth_2"] = ewm_smooth(df, "level_smooth_1", p.span_level_2)
    df.loc[no_price, "level_smooth_2"] = np.nan

    df["velocity_per_day"] = df.groupby("sku")["level_smooth_2"].diff() * GRID_STEPS_PER_DAY
    df["acceleration_per_day"] = df.groupby("sku")["velocity_per_day"].diff() * GRID_STEPS_PER_DAY

    df["window_return"] = df.groupby("sku")["level_smooth_2"].diff(p.window_steps)
    df["down_flag"] = df["window_return"] <= -p.card_threshold
    df["up_flag"] = df["window_return"] >= p.card_threshold
    df.loc[df["window_return"].isna(), ["down_flag", "up_flag"]] = False
    return df


def run(df: pd.DataFrame, *, label: str = "вся категория",
        p: EwmaParams = PARAMS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Возвращает (события, подготовленная панель)."""
    prepared = prepare(df, p)
    events = detect_events(prepared, algorithm=ALGORITHM, label=label,
                           market_threshold=p.market_threshold, min_cards=p.min_cards,
                           n_bootstrap=p.n_bootstrap, ci_alpha=p.ci_alpha,
                           rng=np.random.default_rng(p.random_seed))
    return events, prepared