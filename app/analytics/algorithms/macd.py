# ---------- Алгоритм 2: MACD (импульсы внутри тренда) ----------
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.algorithms.common import detect_events, drop_short_series, ewm_smooth
from app.config.policies import GRID_STEPS_PER_DAY

ALGORITHM = "MACD"


# ---------- Настройки алгоритма ----------
@dataclass(frozen=True)
class MacdParams:
    span_fast: int = 3              # быстрая EMA, шагов сетки
    span_slow: int = 9              # медленная EMA, считается независимо (не каскад)
    span_signal: int = 3            # сглаживание самой macd_line
    window_days: int = 3            # окно нужно для знака направления и величины
    line_threshold: float = 0.0571  # минимум |macd_line| в момент пересечения
    market_threshold: float = 0.04  # порог доли участвующих
    min_points: int = 9             # разгон медленной EMA
    min_cards: int = 25
    n_bootstrap: int = 1000
    ci_alpha: float = 0.05
    random_seed: int = 42

    @property
    def window_steps(self) -> int:
        return self.window_days * GRID_STEPS_PER_DAY


PARAMS = MacdParams()


def prepare(df: pd.DataFrame, p: MacdParams = PARAMS) -> pd.DataFrame:
    df = drop_short_series(df, p.min_points)
    no_price = df["log_price"].isna()

    for col, span in (("ema_fast", p.span_fast), ("ema_slow", p.span_slow)):
        df[col] = ewm_smooth(df, "log_price", span)
        df.loc[no_price, col] = np.nan

    df["macd_line"] = df["ema_fast"] - df["ema_slow"]
    df["signal_line"] = ewm_smooth(df, "macd_line", p.span_signal)
    df.loc[df["macd_line"].isna(), "signal_line"] = np.nan
    df["macd_histogram"] = df["macd_line"] - df["signal_line"]

    df["window_return"] = df.groupby("sku")["ema_fast"].diff(p.window_steps)
    df["velocity_per_day"] = df.groupby("sku")["ema_fast"].diff() * GRID_STEPS_PER_DAY

    # момент (когда) - от пересечения гистограммой нуля,
    # направление (какое) - по фактическому знаку window_return
    df["hist_sign"] = np.sign(df["macd_histogram"])
    prev_sign = df.groupby("sku")["hist_sign"].shift(1)
    crossed = (((prev_sign <= 0) & (df["hist_sign"] > 0))
               | ((prev_sign >= 0) & (df["hist_sign"] < 0)))
    significant = crossed & (df["macd_line"].abs() >= p.line_threshold)

    df["up_flag"] = significant & (df["window_return"] > 0)
    df["down_flag"] = significant & (df["window_return"] < 0)
    df.loc[df["macd_histogram"].isna(), ["up_flag", "down_flag"]] = False
    df.loc[prev_sign.isna(), ["up_flag", "down_flag"]] = False
    df.loc[df["window_return"].isna(), ["up_flag", "down_flag"]] = False
    return df


def run(df: pd.DataFrame, *, label: str = "вся категория",
        p: MacdParams = PARAMS) -> tuple[pd.DataFrame, pd.DataFrame]:
    prepared = prepare(df, p)
    events = detect_events(prepared, algorithm=ALGORITHM, label=label,
                           market_threshold=p.market_threshold, min_cards=p.min_cards,
                           n_bootstrap=p.n_bootstrap, ci_alpha=p.ci_alpha,
                           rng=np.random.default_rng(p.random_seed))
    return events, prepared