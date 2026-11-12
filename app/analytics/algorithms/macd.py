# ---------- Алгоритм 2: MACD (импульсы внутри тренда) ----------
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.algorithms.common import detect_events, drop_short_series, ewm_smooth
from app.config.policies import FALSE_ALARM_MAX, GRID_STEPS_PER_DAY

ALGORITHM = "MACD"


# ---------- Настройки алгоритма ----------
@dataclass(frozen=True)
class MacdParams:
    span_fast: int = 2              # быстрая EMA, шагов сетки
    span_slow: int = 6              # медленная EMA, считается независимо (не каскад)
    span_signal: int = 2            # сглаживание самой macd_line
    window_days: float = 2          # окно нужно для знака направления и величины, суток (4 шага)
    line_threshold: float = 0.01    # минимум |macd_line| в момент пересечения
    # p0: фон - доля карточек с флагом в спокойное время (подбор 12.11 на SS26, AW27).
    # Категория: наименьший фон, при котором в спокойных точках событий нет.
    # Подкатегория и ниша: на малой выборке карточки двигаются вместе, число ложных флагов
    # рассеяно шире биномиального; фон подобран так, чтобы ложных точек было не больше
    # FALSE_ALARM_MAX (замер на 30 карточках: 0,5%)
    background_rate: float = 0.0489         # категория
    background_rate_small: float = 0.1045   # подкатегория и ниша
    min_points: int = 9             # разгон медленной EMA
    min_cards: int = 25
    n_bootstrap: int = 1000
    ci_alpha: float = 0.05
    random_seed: int = 42

    @property
    def window_steps(self) -> int:
        return int(round(self.window_days * GRID_STEPS_PER_DAY))

    def background_rate_for(self, level: str) -> float:
        """Фон для уровня: category - категория, остальные уровни (subcategory, niche) - малая выборка."""
        return self.background_rate if level == "category" else self.background_rate_small


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


def run(df: pd.DataFrame, *, label: str = "вся категория", level: str = "category",
        p: MacdParams = PARAMS, min_cards: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """level - уровень (category | subcategory | niche): от него зависит фон для порога события.
    min_cards - минимум карточек в срезе точки (порог уровня); по умолчанию p.min_cards."""
    prepared = prepare(df, p)
    events = detect_events(prepared, algorithm=ALGORITHM, label=label,
                           background_rate=p.background_rate_for(level), false_alarm=FALSE_ALARM_MAX,
                           min_cards=p.min_cards if min_cards is None else min_cards,
                           n_bootstrap=p.n_bootstrap, ci_alpha=p.ci_alpha,
                           rng=np.random.default_rng(p.random_seed))
    return events, prepared