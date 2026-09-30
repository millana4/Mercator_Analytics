# ---------- Общие инструменты ----------
import numpy as np
import pandas as pd

PRICE_COL = "card_price"


def log_to_pct(log_x):
    return (np.exp(log_x) - 1) * 100


# ---------- 1. Плоская панель с признаками среза ----------
def build_analysis_panel(panel: pd.DataFrame, cards: pd.DataFrame,
                         strata: pd.DataFrame, card_ids: list[str]) -> pd.DataFrame:
    # how="left": у карточек, созданных для ниши, страты нет - признаки среза остаются
    # пустыми, карточка не теряется. У категорий и подкатегорий страта каждой карточки
    # есть в strata, для них результат прежний.
    dims = cards[["card_id", "sku", "stratum_id"]].merge(
        strata[["stratum_id", "gender", "layer", "item", "subcategory"]],
        on="stratum_id", how="left")
    df = panel[panel["card_id"].isin(card_ids)].merge(dims, on=["card_id", "sku"])
    # цена 0 недопустима: ln(0) = -inf. Приводим к обычному float64,
    # иначе pandas ругается на masked-массив. Ноль трактуем как отсутствие цены.
    prices = pd.to_numeric(df[PRICE_COL], errors="coerce").astype("float64")
    df["log_price"] = np.log(prices.where(prices > 0))
    return df.sort_values(["sku", "parsed_at"]).reset_index(drop=True)


def drop_short_series(df: pd.DataFrame, min_points: int) -> pd.DataFrame:
    """Убирает карточки, у которых непустых точек меньше, чем нужно алгоритму."""
    counts = df.groupby("sku")["log_price"].transform("count")
    return df[counts >= min_points].copy()


# ---------- 2. Сглаживание: одна реализация, span приходит от алгоритма ----------
def ewm_smooth(df: pd.DataFrame, source_col: str, span: int) -> pd.Series:
    """EWMA по каждому ряду отдельно. alpha = 2/(span+1)."""
    return df.groupby("sku")[source_col].transform(
        lambda s: s.ewm(span=span, adjust=False, ignore_na=False).mean())


# ---------- 3. Доверительные интервалы ----------
def bootstrap_participation(flags: np.ndarray, *, n_boot: int, alpha: float,
                             rng: np.random.Generator):
    n = len(flags)
    if n == 0:
        return np.nan, np.nan, np.nan
    point = flags.mean()
    idx = rng.integers(0, n, size=(n_boot, n))
    boot = flags[idx].mean(axis=1)
    lo, hi = np.percentile(boot, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return point, lo, hi


def bootstrap_conditional_mean(values: np.ndarray, flags: np.ndarray, *,
                                n_boot: int, alpha: float, rng: np.random.Generator):
    n = len(values)
    if n == 0:
        return np.nan, np.nan, np.nan
    participating = values[flags]
    point = participating.mean() if len(participating) else np.nan
    idx = rng.integers(0, n, size=(n_boot, n))
    boot_values, boot_flags = values[idx], flags[idx]
    counts = boot_flags.sum(axis=1)
    sums = np.where(boot_flags, boot_values, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot_means = np.where(counts > 0, sums / counts, np.nan)
    valid = ~np.isnan(boot_means)
    if valid.sum() < n_boot * 0.5:
        return point, np.nan, np.nan   # мало повторов с участниками - интервал ненадёжен
    lo, hi = np.percentile(boot_means[valid], [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return point, lo, hi


# ---------- 4. Детектор событий: все пороги - аргументы вызова ----------
def detect_events(df: pd.DataFrame, *, algorithm: str, label: str,
                  market_threshold: float, min_cards: int,
                  n_bootstrap: int, ci_alpha: float,
                  rng: np.random.Generator) -> pd.DataFrame:
    """Событие = непрерывный блок точек, где доля карточек с флагом >= порога рынка.

    Возвращает ЧИСЛА в лог-единицах и долях, без форматирования - результат
    пишется в базу. Для чтения глазами есть format_events().
    Метрики блока усредняются, CI считается bootstrap'ом в момент пика.
    """
    n_cards = df["sku"].nunique()
    if n_cards < min_cards:
        return pd.DataFrame()

    events = []
    for direction in ("down", "up"):
        flag_col = f"{direction}_flag"
        ts_rows = []
        for t, slice_df in df.groupby("parsed_at"):
            flags = slice_df[flag_col]
            participating = slice_df.loc[flags]
            ts_rows.append({
                "parsed_at": t,
                "participation_pct": flags.mean(),
                "magnitude_pct": participating["window_return"].mean() if len(participating) else np.nan,
                "speed_pct_per_day": participating["velocity_per_day"].mean() if len(participating) else np.nan,
                "signal_active": flags.mean() >= market_threshold,
            })
        ts = pd.DataFrame(ts_rows)

        active = ts["signal_active"]
        block_id = (active != active.shift(fill_value=False)).cumsum()
        for _, block in ts[active].groupby(block_id[active]):
            peak_t = block.loc[block["participation_pct"].idxmax(), "parsed_at"]
            peak = df[df["parsed_at"] == peak_t]
            peak_flags = peak[flag_col].to_numpy()

            ci_kw = {"n_boot": n_bootstrap, "alpha": ci_alpha, "rng": rng}
            p_point, p_lo, p_hi = bootstrap_participation(peak_flags, **ci_kw)
            m_point, m_lo, m_hi = bootstrap_conditional_mean(
                peak["window_return"].to_numpy(), peak_flags, **ci_kw)
            s_point, s_lo, s_hi = bootstrap_conditional_mean(
                peak["velocity_per_day"].to_numpy(), peak_flags, **ci_kw)

            start, end = block["parsed_at"].min(), block["parsed_at"].max()
            events.append({
                "algorithm": algorithm,
                "label": label,
                "direction": direction,
                "n_cards": n_cards,
                "start_at": start,
                "end_at": end,
                "duration_days": (end - start).total_seconds() / 86400,
                "peak_at": peak_t,
                # доли участвующих - в долях единицы
                "participation_pct": p_point,
                "participation_ci_low": p_lo,
                "participation_ci_high": p_hi,
                # величина и скорость - в ЛОГ-единицах, перевод в проценты при выводе
                "magnitude_mean_log": block["magnitude_pct"].mean(),
                "magnitude_peak_log": m_point,
                "magnitude_ci_low_log": m_lo,
                "magnitude_ci_high_log": m_hi,
                "speed_mean_log": block["speed_pct_per_day"].mean(),
                "speed_peak_log": s_point,
                "speed_ci_low_log": s_lo,
                "speed_ci_high_log": s_hi,
            })

    if not events:
        return pd.DataFrame()
    return pd.DataFrame(events).sort_values("start_at").reset_index(drop=True)


# ---------- 5. Форматирование для чтения глазами ----------
DIRECTION_TITLES = {"down": "Снижение цен", "up": "Рост цен"}


def _ci(low, high, to_pct: bool, digits: int) -> str:
    if pd.isna(low) or pd.isna(high):
        return "-"
    lo, hi = (log_to_pct(low), log_to_pct(high)) if to_pct else (low * 100, high * 100)
    return f"[{lo:.{digits}f}; {hi:.{digits}f}]"


def format_events(events: pd.DataFrame) -> pd.DataFrame:
    """Числовую таблицу событий - в читаемый вид с процентами и интервалами."""
    if events.empty:
        return pd.DataFrame()
    rows = []
    for _, e in events.iterrows():
        rows.append({
            "Алгоритм": e["algorithm"],
            "Тип события": DIRECTION_TITLES[e["direction"]],
            "Снепшот": e["label"],
            "N карточек": e["n_cards"],
            "Начало": e["start_at"],
            "Конец": e["end_at"],
            "Длительность, сут": round(e["duration_days"], 1),
            "Момент пика": e["peak_at"],
            "Пиковая доля участвующих, %": round(e["participation_pct"] * 100, 1),
            "Пиковая доля, CI95 %": _ci(e["participation_ci_low"], e["participation_ci_high"],
                                        to_pct=False, digits=1),
            "Величина изменения в среднем, %": round(log_to_pct(e["magnitude_mean_log"]), 1),
            "Величина изменения на пике, CI95 %": _ci(e["magnitude_ci_low_log"],
                                                     e["magnitude_ci_high_log"],
                                                     to_pct=True, digits=1),
            "Скорость за событие в среднем, %/сутки": round(log_to_pct(e["speed_mean_log"]), 2),
            "Скорость на пике, CI95 %/сутки": _ci(e["speed_ci_low_log"], e["speed_ci_high_log"],
                                                 to_pct=True, digits=2),
        })
    return pd.DataFrame(rows)


# ---------- 6. Участники события: карточки с флагом внутри окна ----------
def find_participants(df: pd.DataFrame, direction: str, start_at, end_at,
                      cards: pd.DataFrame) -> list[dict]:
    """Карточки, у которых флаг направления был True хотя бы в одной точке
    внутри окна события [start_at, end_at]. card_id/name/url берутся из cards,
    потому что в prepared их нет - build_analysis_panel их не подмердживает."""
    flag_col = f"{direction}_flag"
    window = df[(df["parsed_at"] >= start_at) & (df["parsed_at"] <= end_at)]
    skus = window.loc[window[flag_col], "sku"].unique()
    matched = cards[cards["sku"].isin(skus)][["card_id", "sku", "name", "url"]]
    return matched.drop_duplicates("sku").to_dict("records")