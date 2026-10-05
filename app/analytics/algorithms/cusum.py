# ---------- Алгоритм 3: CUSUM (накопление смещения) ----------
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.algorithms.common import detect_events, drop_short_series, ewm_smooth
from app.config.policies import GRID_STEPS_PER_DAY

ALGORITHM = "CUSUM"


# ---------- Настройки алгоритма ----------
@dataclass(frozen=True)
class CusumParams:
    span_level: int = 3             # одно лёгкое сглаживание, не каскад
    window_scale_days: int = 30     # окно оценки локального шума
    min_valid_points: int = 30      # минимум валидных приращений в окне шума
    min_scale: float = 0.0008       # floor масштаба, защита от деления на почти-ноль
    norm_clip: float = 50.0         # страховка, не основная защита
    slack: float = 4.0              # допустимое смещение за шаг
    threshold_up: float = 76.0
    threshold_down: float = 78.0
    window_days: int = 3            # окно для метрики величины изменения
    market_threshold: float = 0.09  # порог доли участвующих
    min_points: int = 31            # окно шума плюс точка сверху
    min_cards: int = 25
    n_bootstrap: int = 1000
    ci_alpha: float = 0.05
    random_seed: int = 42

    @property
    def window_scale_steps(self) -> int:
        return self.window_scale_days * GRID_STEPS_PER_DAY

    @property
    def window_steps(self) -> int:
        return self.window_days * GRID_STEPS_PER_DAY


PARAMS = CusumParams()


def rolling_mad(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    """Медианное абсолютное отклонение в скользящем окне.
    NaN внутри окна игнорируются - иначе одно пустое значение обнуляет всё окно."""
    def mad(values: np.ndarray) -> float:
        clean = values[~np.isnan(values)]
        if len(clean) < min_periods:
            return np.nan
        return np.median(np.abs(clean - np.median(clean)))

    return s.rolling(window=window, min_periods=min_periods).apply(mad, raw=True)


def compute_cusum(values: np.ndarray, slack: float) -> tuple[np.ndarray, np.ndarray]:
    """Две накопительные суммы. NaN ЗАМОРАЖИВАЕТ состояние, а не сбрасывает."""
    n = len(values)
    up, down = np.zeros(n), np.zeros(n)
    for i in range(n):
        prev_up = up[i - 1] if i > 0 else 0.0
        prev_down = down[i - 1] if i > 0 else 0.0
        x = values[i]
        if np.isnan(x):
            up[i], down[i] = prev_up, prev_down
        else:
            up[i] = max(0.0, prev_up + x - slack)
            down[i] = max(0.0, prev_down - x - slack)
    return up, down


def prepare(df: pd.DataFrame, p: CusumParams = PARAMS) -> pd.DataFrame:
    df = drop_short_series(df, p.min_points)
    no_price = df["log_price"].isna()

    df["level_smooth"] = ewm_smooth(df, "log_price", p.span_level)
    df.loc[no_price, "level_smooth"] = np.nan

    df["increment"] = df.groupby("sku")["level_smooth"].diff()

    df["local_scale"] = df.groupby("sku")["increment"].transform(
        lambda s: 1.4826 * rolling_mad(s, p.window_scale_steps, p.min_valid_points))
    df["local_scale"] = df["local_scale"].clip(lower=p.min_scale)

    df["increment_norm"] = (df["increment"] / df["local_scale"]).clip(
        lower=-p.norm_clip, upper=p.norm_clip)

    df = df.sort_values(["sku", "parsed_at"]).reset_index(drop=True)
    up_parts, down_parts = [], []
    for _, group in df.groupby("sku", sort=False):
        up, down = compute_cusum(group["increment_norm"].to_numpy(), p.slack)
        up_parts.append(pd.Series(up, index=group.index))
        down_parts.append(pd.Series(down, index=group.index))
    df["cusum_up"] = pd.concat(up_parts)
    df["cusum_down"] = pd.concat(down_parts)

    df["up_flag"] = df["cusum_up"] >= p.threshold_up
    df["down_flag"] = df["cusum_down"] >= p.threshold_down

    df["window_return"] = df.groupby("sku")["level_smooth"].diff(p.window_steps)
    df["velocity_per_day"] = df["increment"] * GRID_STEPS_PER_DAY
    return df


def run(df: pd.DataFrame, *, label: str = "вся категория",
        p: CusumParams = PARAMS, min_cards: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """min_cards - минимум карточек в срезе точки (порог уровня); по умолчанию p.min_cards."""
    prepared = prepare(df, p)
    events = detect_events(prepared, algorithm=ALGORITHM, label=label,
                           market_threshold=p.market_threshold,
                           min_cards=p.min_cards if min_cards is None else min_cards,
                           n_bootstrap=p.n_bootstrap, ci_alpha=p.ci_alpha,
                           rng=np.random.default_rng(p.random_seed))
    return events, prepared