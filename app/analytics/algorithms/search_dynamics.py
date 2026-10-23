# ---------- Анализ поисковой выдачи: повторяемость за сутки, индикатор, погода ----------
import math

import numpy as np
import pandas as pd

from app.config import policies

# σ = MAD / Φ⁻¹(0,75) при нормальном распределении: 1 / 0,6745
MAD_TO_SIGMA = 1.4826

STATUS_FEW_STRATA = "few_strata"   # мало общих страт - точки нет
STATUS_WARMUP = "warmup"           # разгон: история нормы ещё набирается
STATUS_NORMAL = "normal"
STATUS_LOW = "low"                 # повторяемость ниже обычной - выдача изменилась сильнее
STATUS_HIGH = "high"               # повторяемость выше обычной

REPEAT_COLUMNS = ["run_at", "prev_run", "strata_n", "repeat_pct", "enough_strata"]
INDICATOR_COLUMNS = ["run_at", "prev_run", "strata_n", "repeat_pct",
                     "normal_pct", "band_low", "band_high", "z", "status"]
WEATHER_COLUMNS = ["run_at", "temp_mean", "temp_now", "temp_min", "temp_max", "precip",
                   "cloud_mean", "sky", "precip_kind"]

# Значок погоды суток: небо по облачности, тип осадков по кодам погоды WMO
SKY_CLEAR, SKY_PARTLY, SKY_CLOUDY = "clear", "partly", "cloudy"
PRECIP_NONE, PRECIP_RAIN, PRECIP_SNOW, PRECIP_MIXED = "none", "rain", "snow", "mixed"
# морось, дождь, ледяной дождь, ливни, гроза
RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99})
# снег, снежные зёрна, снегопад-ливень
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})


def daily_snapshots(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Только суточный снимок SEARCH_DAILY_TIME; снимки в другое время не берутся."""
    hour, minute = policies.SEARCH_DAILY_TIME
    run_at = snapshots["run_at"]
    return snapshots[(run_at.dt.hour == hour) & (run_at.dt.minute == minute)]


def repeatability_24h(snapshots: pd.DataFrame, strata_total: int) -> pd.DataFrame:
    """Повторяемость суточного снимка к снимку ровно на SEARCH_GAP_HOURS раньше,
    по общим стратам: R = Σ|A∩B| / Σ|A| · 100%.

    snapshots - плитки выдачи: run_at, stratum_id, sku. strata_total - страт в сете.
    enough_strata - общих страт не меньше SEARCH_MIN_STRATA_SHARE от strata_total.
    """
    daily = daily_snapshots(snapshots)
    if daily.empty:
        return pd.DataFrame(columns=REPEAT_COLUMNS)

    # Множества SKU собираются явно: для строкового типа pandas («string»,
    # как в канонической схеме) groupby().agg(set) возвращает списки, а не множества.
    by_run: dict[pd.Timestamp, dict[str, set]] = {}
    for (run_at, stratum_id), skus in daily.groupby(["run_at", "stratum_id"])["sku"]:
        by_run.setdefault(pd.Timestamp(run_at), {})[str(stratum_id)] = set(skus)
    gap = pd.Timedelta(hours=policies.SEARCH_GAP_HOURS)
    min_strata = math.ceil(policies.SEARCH_MIN_STRATA_SHARE * strata_total)

    rows = []
    for run_at in sorted(by_run):
        prev_run = run_at - gap
        if prev_run not in by_run:
            continue
        prev, cur = by_run[prev_run], by_run[run_at]
        common = prev.keys() & cur.keys()
        kept = sum(len(prev[s] & cur[s]) for s in common)
        total = sum(len(prev[s]) for s in common)
        rows.append({"run_at": run_at, "prev_run": prev_run, "strata_n": len(common),
                     "repeat_pct": kept / total * 100 if total else np.nan,
                     "enough_strata": total > 0 and len(common) >= min_strata})
    return pd.DataFrame(rows, columns=REPEAT_COLUMNS)


def repeatability_indicator(rep: pd.DataFrame) -> pd.DataFrame:
    """Для каждого дня: обычная повторяемость, коридор, z и статус.

    Обычная - медиана последних SEARCH_REP_WINDOW нормальных дней, разброс -
    MAD x MAD_TO_SIGMA, не меньше SEARCH_REP_SCALE_FLOOR. Дни с отклонением
    в историю нормы не попадают, иначе провал раздувает коридор.
    """
    history: list[float] = []
    rows = []
    for r in rep.itertuples():
        row = {"run_at": r.run_at, "prev_run": r.prev_run, "strata_n": r.strata_n,
               "repeat_pct": r.repeat_pct, "normal_pct": np.nan, "band_low": np.nan,
               "band_high": np.nan, "z": np.nan}
        if not r.enough_strata:
            row["status"] = STATUS_FEW_STRATA
        elif len(history) < policies.SEARCH_REP_MIN_DAYS:
            row["status"] = STATUS_WARMUP
            history.append(r.repeat_pct)
        else:
            window = np.array(history[-policies.SEARCH_REP_WINDOW:])
            normal = float(np.median(window))
            scale = max(MAD_TO_SIGMA * float(np.median(np.abs(window - normal))),
                        policies.SEARCH_REP_SCALE_FLOOR)
            z = (r.repeat_pct - normal) / scale
            k = policies.SEARCH_REP_K
            row.update(normal_pct=normal, band_low=normal - k * scale,
                       band_high=normal + k * scale, z=z)
            if z <= -k:
                row["status"] = STATUS_LOW
            elif z >= k:
                row["status"] = STATUS_HIGH
            else:
                row["status"] = STATUS_NORMAL
                history.append(r.repeat_pct)
        rows.append(row)
    return pd.DataFrame(rows, columns=INDICATOR_COLUMNS)


def sky_kind(cloud_mean) -> str | None:
    """Небо суток по средней облачности, %: ясно, переменная облачность, облачно."""
    if pd.isna(cloud_mean):
        return None
    if cloud_mean < policies.WEATHER_SKY_CLEAR_MAX:
        return SKY_CLEAR
    if cloud_mean <= policies.WEATHER_SKY_PARTLY_MAX:
        return SKY_PARTLY
    return SKY_CLOUDY


def precip_kind(day: pd.DataFrame) -> str:
    """Осадки суток: меньше WEATHER_PRECIP_MIN_MM - нет; иначе тип по кодам погоды часов
    с осадками; коды не про осадки - по средней температуре этих часов (<= 0 - снег)."""
    total = day["precip"].sum()
    if pd.isna(total) or total < policies.WEATHER_PRECIP_MIN_MM:
        return PRECIP_NONE
    wet = day[day["precip"] > 0]
    codes = {int(c) for c in wet["code"].dropna()} if "code" in wet.columns else set()
    rain, snow = bool(codes & RAIN_CODES), bool(codes & SNOW_CODES)
    if rain and snow:
        return PRECIP_MIXED
    if snow:
        return PRECIP_SNOW
    if rain:
        return PRECIP_RAIN
    temp = wet["temp"].mean()
    return PRECIP_SNOW if pd.notna(temp) and temp <= 0 else PRECIP_RAIN


def weather_for_runs(hourly: pd.DataFrame, runs) -> pd.DataFrame:
    """Погода к каждому снимку за WEATHER_WINDOW_HOURS до него: средняя, минимум и максимум
    температуры, сумма осадков, температура в час снимка, средняя облачность и значок:
    небо (sky) и тип осадков (precip_kind).
    hourly - time (местное время), temp (°C), precip (мм за час), cloud (%), code (WMO)."""
    window = pd.Timedelta(hours=policies.WEATHER_WINDOW_HOURS)
    has_cloud = "cloud" in hourly.columns
    rows = []
    for run_at in runs:
        run_at = pd.Timestamp(run_at)
        day = hourly[(hourly["time"] > run_at - window) & (hourly["time"] <= run_at)]
        now = hourly[hourly["time"] == run_at.floor("h")]
        has_day = len(day) > 0
        cloud_mean = day["cloud"].mean() if has_day and has_cloud else np.nan
        rows.append({
            "run_at": run_at,
            "temp_mean": day["temp"].mean() if has_day else np.nan,
            "temp_now": now["temp"].iloc[0] if len(now) else np.nan,
            "temp_min": day["temp"].min() if has_day else np.nan,
            "temp_max": day["temp"].max() if has_day else np.nan,
            "precip": day["precip"].sum() if has_day else np.nan,
            "cloud_mean": cloud_mean,
            "sky": sky_kind(cloud_mean),
            "precip_kind": precip_kind(day) if has_day else None,
        })
    return pd.DataFrame(rows, columns=WEATHER_COLUMNS)