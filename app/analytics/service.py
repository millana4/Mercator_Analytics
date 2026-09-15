# ---------- Оркестратор расчёта: все уровни, все алгоритмы, полное логирование ----------
import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.analytics.algorithms import common, cusum, ewma, macd
from app.analytics.selection import Selection, select_for_category, select_for_subcategory
from app.assignments.models import Assignment
from app.config import policies

logger = logging.getLogger(__name__)

ALGORITHMS = (ewma, macd, cusum)


@dataclass
class LevelResult:
    """Результат одного среза: уровень + ключ + алгоритм."""
    set_id: str
    category: str
    level: str                      # category | subcategory | item
    level_key: str                  # название среза
    algorithm: str
    n_cards: int
    total_quota: int
    deficit_share: float
    scale_factor: float
    events: pd.DataFrame
    curve: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    warning: str | None = None
    computed_at: datetime = field(default_factory=datetime.now)


def _log_selection(level: str, level_key: str, algorithm: str, sel: Selection) -> None:
    """Компактная запись о том, на какой выборке считался алгоритм."""
    if sel.scale_factor < 1.0:
        status = f"урезано пропорционально, коэффициент {sel.scale_factor:.3f}"
    elif sel.deficit:
        status = "дефицит в пределах допустимого"
    else:
        status = "полная выборка"

    logger.info("[%s/%s] %s: карточек=%d квота=%d дефицит=%.1f%% (%d шт) - %s",
                level, level_key, algorithm, len(sel.card_ids), sel.total_quota,
                sel.deficit_share * 100, sel.deficit, status)

    ranked = sorted(sel.shortfalls, key=lambda x: -x.deficit)
    for s in sorted(sel.shortfalls, key=lambda x: -x.deficit):
        logger.info("    недобор: %s (%s) нужно=%d взято=%d причина=%s",
                    s.level_key, s.gender, s.needed, s.taken, s.reason)


# Колонка сглаженного уровня, по которой строится кривая рынка на графике
LEVEL_COLUMNS = {"EWMA": "level_smooth_2", "MACD": "ema_fast", "CUSUM": "level_smooth"}


def _run_one(module, df: pd.DataFrame, *, set_id: str, category: str, level: str,
             level_key: str, sel: Selection) -> LevelResult:
    events, prepared = module.run(df.copy(), label=level_key)
    n_cards = prepared["sku"].nunique()

    logger.info("[%s/%s] %s: событий=%d, карточек в расчёте=%d",
                level, level_key, module.ALGORITHM, len(events), n_cards)
    for _, e in events.iterrows():
        logger.info("    событие: %s %s - %s (%.1f сут), пик %.1f%%, изменение %.1f%%",
                    common.DIRECTION_TITLES[e["direction"]], e["start_at"], e["end_at"],
                    e["duration_days"], e["participation_pct"] * 100,
                    common.log_to_pct(e["magnitude_mean_log"]))

    level_col = LEVEL_COLUMNS[module.ALGORITHM]
    curve = (prepared.groupby("parsed_at")[level_col].mean()
             .reset_index().rename(columns={level_col: "level_value"}))

    return LevelResult(
        set_id=set_id, category=category, level=level, level_key=level_key,
        algorithm=module.ALGORITHM, n_cards=n_cards, total_quota=sel.total_quota,
        deficit_share=sel.deficit_share, scale_factor=sel.scale_factor,
        events=events, curve=curve, warning=sel.warning_text(level_key),
    )

# ---------- 1. Уровень категории ----------
def run_category(assignment: Assignment, *, set_id: str, category: str,
                 strata: pd.DataFrame, cards: pd.DataFrame,
                 panel: pd.DataFrame) -> list[LevelResult]:
    results = []
    for module in ALGORITHMS:
        p = module.PARAMS
        sel = select_for_category(assignment, strata, cards, panel,
                                  min_points=p.min_points,
                                  min_cards=policies.MIN_CARDS_CATEGORY)
        if not sel.sufficient:
            logger.warning("[category/%s] %s: недостаточно данных - %s",
                           category, module.ALGORITHM, sel.insufficient_reason)
            continue

        _log_selection("category", category, module.ALGORITHM, sel)
        df = common.build_analysis_panel(panel, cards, strata, sel.card_ids)
        results.append(_run_one(module, df, set_id=set_id, category=category,
                                level="category", level_key=category, sel=sel))
    return results


# ---------- 2. Уровень подкатегории ----------
def run_subcategories(assignment: Assignment, *, set_id: str, category: str,
                      strata: pd.DataFrame, cards: pd.DataFrame,
                      panel: pd.DataFrame) -> list[LevelResult]:
    tracked = assignment.tracked_subcategories()
    logger.info("[subcategory] отслеживаемых подкатегорий в задании: %d", len(tracked))

    results = []
    for key in tracked:
        gender, layer, title = key
        label = f"{title} ({gender}, слой {layer})"
        for module in ALGORITHMS:
            p = module.PARAMS
            sel = select_for_subcategory(key, strata, cards, panel,
                                         min_points=p.min_points,
                                         min_cards=policies.MIN_CARDS_SUBCATEGORY)
            if not sel.sufficient:
                logger.warning("[subcategory/%s] %s: недостаточно данных - %s",
                               label, module.ALGORITHM, sel.insufficient_reason)
                continue

            _log_selection("subcategory", label, module.ALGORITHM, sel)
            df = common.build_analysis_panel(panel, cards, strata, sel.card_ids)
            results.append(_run_one(module, df, set_id=set_id, category=category,
                                    level="subcategory", level_key=label, sel=sel))
    return results

# ---------- 3. Весь сет целиком ----------
def run_for_set(connector, assignment: Assignment, set_row: pd.Series) -> list[LevelResult]:
    set_id, category = set_row["set_id"], set_row["category"]
    logger.info("=" * 70)
    logger.info("Расчёт сета: %s / %s / %s (set_id=%s)",
                set_row["marketplace"], category, set_row["geo"], set_id)

    from app.etl.regularize import build_regular_panel

    strata = connector.get_strata(set_id)
    cards = connector.get_cards(set_id)
    skus = cards.loc[cards["status"] == "active", "sku"].dropna().astype(int).tolist()
    logger.info("Страт=%d, карточек active=%d", len(strata), len(skus))

    measurements = connector.get_measurements(skus)
    panel = build_regular_panel(measurements)
    logger.info("Панель: точек сетки=%d, период %s - %s, доля снятых замеров=%.1f%%",
                panel["parsed_at"].nunique(), panel["parsed_at"].min(),
                panel["parsed_at"].max(), panel["is_observed"].mean() * 100)

    results = run_category(assignment, set_id=set_id, category=category,
                           strata=strata, cards=cards, panel=panel)
    results += run_subcategories(assignment, set_id=set_id, category=category,
                                 strata=strata, cards=cards, panel=panel)

    logger.info("Расчёт сета завершён: срезов посчитано=%d", len(results))
    return results