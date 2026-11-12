# ---------- Алгоритм 1: производные по каскаду EWMA (основной детектор) ----------
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.algorithms.common import detect_events, drop_short_series, ewm_smooth
from app.config.policies import FALSE_ALARM_MAX, GRID_STEPS_PER_DAY

ALGORITHM = "EWMA"


# ---------- Настройки алгоритма ----------
@dataclass(frozen=True)
class EwmaParams:
    span_level_1: int = 1           # первое сглаживание, шагов сетки (1 - без сглаживания)
    span_level_2: int = 2           # второе сглаживание, поверх первого
    window_days: float = 1.5        # окно накопленного изменения, суток (1,5 суток = 3 шага)
    card_threshold: float = 0.04    # порог карточки за окно, лог-единицы (~4,1%)
    # p0: фон - доля карточек с флагом в спокойное время (подбор 12.11 на SS26, AW27).
    # Категория: наименьший фон, при котором в спокойных точках событий нет.
    # Подкатегория и ниша: на малой выборке карточки двигаются вместе, число ложных флагов
    # рассеяно шире биномиального; фон подобран так, чтобы ложных точек было не больше
    # FALSE_ALARM_MAX (замер на 30 карточках: 0,8%)
    background_rate: float = 0.1273         # категория
    background_rate_small: float = 0.2015   # подкатегория и ниша
    min_points: int = 5             # каскаду хватает пяти точек
    min_cards: int = 25             # минимум карточек в срезе
    n_bootstrap: int = 1000
    ci_alpha: float = 0.05
    random_seed: int = 42

    @property
    def window_steps(self) -> int:
        return int(round(self.window_days * GRID_STEPS_PER_DAY))

    def background_rate_for(self, level: str) -> float:
        """Фон для уровня: category - категория, остальные уровни (subcategory, niche) - малая выборка."""
        return self.background_rate if level == "category" else self.background_rate_small


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


def run(df: pd.DataFrame, *, label: str = "вся категория", level: str = "category",
        p: EwmaParams = PARAMS, min_cards: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Возвращает (события, подготовленная панель).
    level - уровень (category | subcategory | niche): от него зависит фон для порога события.
    min_cards - минимум карточек в срезе точки (порог уровня); по умолчанию p.min_cards."""
    prepared = prepare(df, p)
    events = detect_events(prepared, algorithm=ALGORITHM, label=label,
                           background_rate=p.background_rate_for(level), false_alarm=FALSE_ALARM_MAX,
                           min_cards=p.min_cards if min_cards is None else min_cards,
                           n_bootstrap=p.n_bootstrap, ci_alpha=p.ci_alpha,
                           rng=np.random.default_rng(p.random_seed))
    return events, prepared