# ---------- Оркестратор расчёта: все уровни, все алгоритмы, полное логирование ----------
import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.analytics.algorithms import common, ewma, macd
from app.analytics.selection import (Selection, select_for_category, select_for_niche,
                                     select_for_subcategory)
from app.assignments.models import Assignment
from app.config import policies
from app.connectors import schema

logger = logging.getLogger(__name__)

ALGORITHMS = (ewma, macd)


@dataclass
class LevelResult:
    """Результат одного снепшота: уровень + ключ + алгоритм."""
    set_id: str                     # id сета; у уровня niche - id ниши
    category: str
    level: str                      # category | subcategory | item | niche
    level_key: str                  # название среза; у уровня niche - id ниши
    algorithm: str
    n_cards: int = 0
    total_quota: int = 0
    deficit_share: float = 0.0
    scale_factor: float = 1.0
    sufficient: bool = True
    insufficient_reason: str | None = None
    events: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
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


def _log_panel(panel: pd.DataFrame) -> None:
    """Сводка по регулярной панели: период и доля годных точек (замер за 24 ч с ценой)."""
    logger.info("Панель: точек сетки=%d, период %s - %s, доля годных точек=%.1f%%",
                panel["parsed_at"].nunique(), panel["parsed_at"].min(),
                panel["parsed_at"].max(), panel["is_valid"].astype(bool).mean() * 100)


# Колонка сглаженного уровня, по которой строится кривая рынка на графике
LEVEL_COLUMNS = {"EWMA": "level_smooth_2", "MACD": "ema_fast"}


def _run_one(module, df: pd.DataFrame, *, set_id: str, category: str, level: str,
             level_key: str, sel: Selection, cards: pd.DataFrame, min_cards: int,
             warning_label: str | None = None) -> LevelResult:
    """Один алгоритм на одной выборке. Достаточность проверяется в каждой точке
    сетки: точка участвует в событиях и кривой, только если в срезе не меньше
    порога карточек (максимум из порога алгоритма и гейта уровня). Пригодных
    точек меньше min_points алгоритма - результат неуспешный."""
    slice_min = max(module.PARAMS.min_cards, min_cards)
    events, prepared = module.run(df.copy(), label=level_key, min_cards=slice_min)
    n_cards = prepared["sku"].nunique()

    ok_points = common.valid_points(prepared, slice_min)
    min_points = module.PARAMS.min_points
    if len(ok_points) < min_points:
        reason = (f"точек сетки, где в срезе не меньше {slice_min} карточек, "
                  f"{len(ok_points)} при минимуме {min_points}")
        logger.warning("[%s/%s] %s: недостаточно данных - %s",
                       level, level_key, module.ALGORITHM, reason)
        return LevelResult(
            set_id=set_id, category=category, level=level, level_key=level_key,
            algorithm=module.ALGORITHM, n_cards=n_cards, total_quota=sel.total_quota,
            sufficient=False, insufficient_reason=reason,
            warning=f"Недостаточно данных для аналитики {warning_label or level_key}: {reason}")

    if len(events):
        events = events.copy()
        events["participants"] = events.apply(
            lambda e: common.find_participants(
                prepared, e["direction"], e["start_at"], e["end_at"], cards),
            axis=1)

    logger.info("[%s/%s] %s: событий=%d, карточек в расчёте=%d, точек со срезом от %d карточек=%d",
                level, level_key, module.ALGORITHM, len(events), n_cards, slice_min, len(ok_points))
    for _, e in events.iterrows():
        logger.info("    событие: %s %s - %s (%.1f сут), пик %.1f%%, изменение %.1f%%",
                    common.DIRECTION_TITLES[e["direction"]], e["start_at"], e["end_at"],
                    e["duration_days"], e["participation_pct"] * 100,
                    common.log_to_pct(e["magnitude_mean_log"]))

    # кривая рынка - только по точкам с достаточным срезом
    level_col = LEVEL_COLUMNS[module.ALGORITHM]
    in_slice = prepared[prepared["parsed_at"].isin(ok_points)]
    curve = (in_slice.groupby("parsed_at")[level_col].mean()
             .reset_index().rename(columns={level_col: "level_value"}))

    return LevelResult(
        set_id=set_id, category=category, level=level, level_key=level_key,
        algorithm=module.ALGORITHM, n_cards=n_cards, total_quota=sel.total_quota,
        deficit_share=sel.deficit_share, scale_factor=sel.scale_factor,
        events=events, curve=curve, warning=sel.warning_text(warning_label or level_key),
    )

# ---------- 1. Уровень категории ----------
def run_category(assignment: Assignment, *, set_id: str, category: str,
                 strata: pd.DataFrame, cards: pd.DataFrame,
                 panel: pd.DataFrame, as_of=None) -> list[LevelResult]:
    results = []
    for module in ALGORITHMS:
        p = module.PARAMS
        sel = select_for_category(assignment, strata, cards, panel,
                                  min_points=p.min_points,
                                  min_cards=policies.MIN_CARDS_CATEGORY, as_of=as_of)
        if not sel.sufficient:
            logger.warning("[category/%s] %s: недостаточно данных - %s",
                           category, module.ALGORITHM, sel.insufficient_reason)
            results.append(LevelResult(
                set_id=set_id, category=category, level="category", level_key=category,
                algorithm=module.ALGORITHM, total_quota=sel.total_quota,
                sufficient=False, insufficient_reason=sel.insufficient_reason,
                warning=sel.warning_text(category)))
            continue

        _log_selection("category", category, module.ALGORITHM, sel)
        df = common.build_analysis_panel(panel, cards, strata, sel.card_ids)
        results.append(_run_one(module, df, set_id=set_id, category=category,
                                level="category", level_key=category, sel=sel,
                                cards=cards, min_cards=policies.MIN_CARDS_CATEGORY))
    return results


# ---------- 2. Уровень подкатегории ----------
def run_subcategories(assignment: Assignment, *, set_id: str, category: str,
                      strata: pd.DataFrame, cards: pd.DataFrame,
                      panel: pd.DataFrame, as_of=None) -> list[LevelResult]:
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
                                         min_cards=policies.MIN_CARDS_SUBCATEGORY,
                                         as_of=as_of)
            if not sel.sufficient:
                logger.warning("[subcategory/%s] %s: недостаточно данных - %s",
                               label, module.ALGORITHM, sel.insufficient_reason)
                results.append(LevelResult(
                    set_id=set_id, category=category, level="subcategory", level_key=label,
                    algorithm=module.ALGORITHM, total_quota=sel.total_quota,
                    sufficient=False, insufficient_reason=sel.insufficient_reason,
                    warning=sel.warning_text(label)))
                continue

            _log_selection("subcategory", label, module.ALGORITHM, sel)
            df = common.build_analysis_panel(panel, cards, strata, sel.card_ids)
            results.append(_run_one(module, df, set_id=set_id, category=category,
                                    level="subcategory", level_key=label, sel=sel,
                                    cards=cards, min_cards=policies.MIN_CARDS_SUBCATEGORY))
    return results

# ---------- 3. Весь сет целиком ----------
def run_for_set(connector, assignment: Assignment, set_row: pd.Series,
                grid_point_at: datetime | None = None) -> list[LevelResult]:
    """grid_point_at - точка расчёта: сетка панели заканчивается на ней, годность
    карточек проверяется по ней. None - последняя точка по замерам."""
    set_id, category = set_row["set_id"], set_row["category"]
    logger.info("=" * 70)
    logger.info("Расчёт сета: %s / %s / %s (set_id=%s), точка расчёта %s",
                set_row["marketplace"], category, set_row["geo"], set_id, grid_point_at)

    from app.etl.regularize import build_regular_panel

    strata = connector.get_strata(set_id)
    cards = connector.get_cards(set_id)
    skus = cards.loc[cards["status"] == "active", "sku"].dropna().astype(int).tolist()
    logger.info("Страт=%d, карточек active=%d", len(strata), len(skus))

    measurements = connector.get_measurements(skus)
    panel = build_regular_panel(measurements, date_to=grid_point_at)
    _log_panel(panel)

    results = run_category(assignment, set_id=set_id, category=category,
                           strata=strata, cards=cards, panel=panel, as_of=grid_point_at)
    results += run_subcategories(assignment, set_id=set_id, category=category,
                                 strata=strata, cards=cards, panel=panel, as_of=grid_point_at)

    logger.info("Расчёт сета завершён: снепшотов посчитано=%d", len(results))
    return results


# ---------- 4. Товарная ниша ----------
def run_for_niche(connector, niche_row: pd.Series,
                  grid_point_at: datetime | None = None) -> list[LevelResult]:
    """Ниша - отдельный срез: состав из ядра (niche_card), отбор по близости
    к референсу, те же алгоритмы, что у категорий. set_id и level_key = id ниши.
    grid_point_at - точка расчёта, как в run_for_set."""
    niche_id, category = niche_row["niche_id"], niche_row["category"]
    query = niche_row["search_query"]
    label = f"ниши «{query}»" if not pd.isna(query) and query else "ниши"
    target_count = int(niche_row["target_count"])

    logger.info("=" * 70)
    logger.info("Расчёт ниши: %s / %s / %s, запрос «%s», статус в ядре %s (niche_id=%s), "
                "точка расчёта %s",
                niche_row["marketplace"], category, niche_row["geo"],
                query, niche_row["status"], niche_id, grid_point_at)

    from app.etl.regularize import build_regular_panel

    cards = connector.get_niche_cards(niche_id)
    skus = cards.loc[cards["status"] == "active", "sku"].dropna().astype(int).tolist()
    logger.info("Карточек в нише=%d, active=%d", len(cards), len(skus))

    measurements = (connector.get_measurements(skus) if skus
                    else schema.empty(schema.MEASUREMENTS))
    panel = build_regular_panel(measurements, date_to=grid_point_at)
    if not panel.empty:
        _log_panel(panel)

    # Признаки страты уровню ниши не нужны, а у карточек, созданных для ниши,
    # страты нет вовсе. build_analysis_panel оставит эти колонки пустыми.
    no_strata = schema.empty(schema.STRATA)

    results = []
    for module in ALGORITHMS:
        p = module.PARAMS
        if panel.empty:
            sel = Selection(card_ids=[], total_quota=target_count, sufficient=False,
                            insufficient_reason="нет замеров по активным карточкам ниши")
        else:
            sel = select_for_niche(cards, panel, target_count=target_count,
                                   min_points=p.min_points,
                                   min_cards=policies.MIN_CARDS_NICHE, as_of=grid_point_at)
        if not sel.sufficient:
            logger.warning("[niche/%s] %s: недостаточно данных - %s",
                           niche_id, module.ALGORITHM, sel.insufficient_reason)
            results.append(LevelResult(
                set_id=niche_id, category=category, level="niche", level_key=niche_id,
                algorithm=module.ALGORITHM, total_quota=sel.total_quota,
                sufficient=False, insufficient_reason=sel.insufficient_reason,
                warning=sel.warning_text(label)))
            continue

        _log_selection("niche", niche_id, module.ALGORITHM, sel)
        df = common.build_analysis_panel(panel, cards, no_strata, sel.card_ids)
        results.append(_run_one(module, df, set_id=niche_id, category=category,
                                level="niche", level_key=niche_id, sel=sel,
                                cards=cards, min_cards=policies.MIN_CARDS_NICHE,
                                warning_label=label))

    logger.info("Расчёт ниши завершён: снепшотов посчитано=%d", len(results))
    return results