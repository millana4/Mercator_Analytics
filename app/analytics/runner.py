# ---------- Запуск полного расчёта и запись результатов в базу ----------
import logging
from datetime import datetime, timedelta

import pandas as pd

from app.analytics.active_sets import CALC_STATUSES, SHOWN_STATUS
from app.analytics.search_service import run_search_for_set
from app.analytics.service import run_for_niche, run_for_set
from app.assignments.core import assignment_from_strata
from app.assignments.models import Assignment
from app.assignments.readers import read_assignment
from app.config import policies
from app.config.settings import get_settings
from app.connectors.factory import get_connector
from app.db.repository import save_run
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Файл задания - только у категорий, задание которых не ведётся в ядре (одежда).
# Остальные (детская одежда) берут квоты и «Отслеживать подкатегорию» из страт сета в ядре.
ASSIGNMENT_FILES = {
    "clothing": "represent_set_clothes.csv",
}

# Параметры алгоритма, которые сохраняются вместе с расчётом
from app.analytics.algorithms import ewma, macd

PARAMS_BY_ALGORITHM = {
    ewma.ALGORITHM: ewma.PARAMS,
    macd.ALGORITHM: macd.PARAMS,
}

# Ниши в этих статусах ядра не считаются: удалённые и остановленные ошибкой.
# Остальные считаются всегда - недостаток карточек и замеров отсекает гейт.
NICHE_SKIPPED_STATUSES = ("deleted", "failed")


def current_grid_point(now: datetime | None = None) -> datetime:
    """Ближайшая прошедшая точка сетки. Расчёт всегда привязан к ней,
    а не к моменту запуска - иначе повторный прогон создаст дубль."""
    now = now or datetime.now()
    candidates = [now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                  for t in policies.grid_times()]
    past = [c for c in candidates if c <= now]
    if past:
        return max(past)
    # до первой точки суток - берём последнюю точку вчерашнего дня
    yesterday = now - timedelta(days=1)
    last = policies.grid_times()[-1]
    return yesterday.replace(hour=last.hour, minute=last.minute, second=0, microsecond=0)


def load_assignment(connector, set_row: pd.Series, data_dir: str) -> Assignment | None:
    """Задание сета: из файла - для категорий из ASSIGNMENT_FILES, иначе из страт сета в ядре."""
    category = set_row["category"]
    name = ASSIGNMENT_FILES.get(category)
    if name is None:
        return assignment_from_strata(connector.get_strata(set_row["set_id"]), category,
                                      source_name=f"ядро, сет {set_row['set_id']}")
    path = f"{data_dir}/{name}"
    try:
        with open(path, "rb") as f:
            return read_assignment(f.read(), name, category)
    except FileNotFoundError:
        logger.error("Файл задания не найден: %s", path)
        return None


def run_all(grid_point_at: datetime | None = None) -> int:
    """Полный цикл: все сеты и все ниши, все уровни, все алгоритмы. Результат - в базу.
    Возвращает число сохранённых снепшотов."""
    grid_point_at = grid_point_at or current_grid_point()
    settings = get_settings()
    connector = get_connector()

    logger.info("#" * 70)
    logger.info("ЗАПУСК РАСЧЁТА за точку сетки %s (источник: %s)",
                grid_point_at, settings.connector_kind)

    sets = connector.get_sets()
    # черновики и выведенные сеты не считаются; сет в сборе считается, но не показывается
    calc_sets = sets[sets["status"].isin(CALC_STATUSES)]
    skipped = sets[~sets["status"].isin(CALC_STATUSES)]
    if len(skipped):
        logger.info("Сеты без расчёта (статус): %s",
                    ", ".join(f"{r.set_id} ({r.status})" for r in skipped.itertuples()))
    saved = 0

    for _, row in calc_sets.iterrows():
        assignment = load_assignment(connector, row, settings.mock_data_dir)
        if assignment is None:
            continue
        try:
            results = run_for_set(connector, assignment, row, grid_point_at)
        except Exception:
            logger.exception("Расчёт сета %s провалился, продолжаем со следующим",
                             row["set_id"])
            continue

        with SessionLocal() as session:
            for result in results:
                try:
                    save_run(session, result, marketplace=row["marketplace"],
                             geo=row["geo"], grid_point_at=grid_point_at,
                             params=PARAMS_BY_ALGORITHM.get(result.algorithm))
                    saved += 1
                except Exception:
                    logger.exception("Не удалось сохранить снепшот %s/%s/%s",
                                     result.level, result.level_key, result.algorithm)
            session.commit()

            if policies.DEBUG_CHARTS:
                _save_debug_charts(results, row, grid_point_at)

    _run_search(connector, sets[sets["status"] == SHOWN_STATUS])
    saved += _run_niches(connector, grid_point_at)

    logger.info("РАСЧЁТ ЗАВЕРШЁН: снепшотов сохранено=%d", saved)
    return saved


def _run_search(connector, sets: pd.DataFrame) -> None:
    """Анализ поисковой выдачи по активным сетам (выдача снимается только по ним).
    Ряд сета пересчитывается целиком; ошибка одного сета не останавливает расчёт."""
    for _, row in sets.iterrows():
        try:
            with SessionLocal() as session:
                run_search_for_set(connector, session, row)
                session.commit()
        except Exception:
            logger.exception("Анализ выдачи сета %s провалился, продолжаем", row["set_id"])


def _run_niches(connector, grid_point_at: datetime) -> int:
    """Все ниши за ту же точку сетки. Возвращает число сохранённых снепшотов."""
    try:
        niches = connector.get_niches()
    except Exception:
        logger.exception("Не удалось получить ниши из источника - расчёт ниш пропущен")
        return 0
    niches = niches[~niches["status"].isin(NICHE_SKIPPED_STATUSES)]
    logger.info("Ниш к расчёту: %d", len(niches))

    saved = 0
    for _, row in niches.iterrows():
        try:
            results = run_for_niche(connector, row, grid_point_at)
        except Exception:
            logger.exception("Расчёт ниши %s провалился, продолжаем со следующей",
                             row["niche_id"])
            continue

        with SessionLocal() as session:
            for result in results:
                try:
                    save_run(session, result, marketplace=row["marketplace"],
                             geo=row["geo"], grid_point_at=grid_point_at,
                             params=PARAMS_BY_ALGORITHM.get(result.algorithm))
                    saved += 1
                except Exception:
                    logger.exception("Не удалось сохранить снепшот %s/%s/%s",
                                     result.level, result.level_key, result.algorithm)
            session.commit()

            if policies.DEBUG_CHARTS:
                _save_debug_charts(results, row, grid_point_at)
    return saved


def _save_debug_charts(results, set_row, grid_point_at: datetime) -> None:
    """Складывает PNG по всем снепшотам сета в отладочный каталог."""
    from app.viz.charts import render_chart, save_debug_chart

    saved = 0
    for result in results:
        if not result.sufficient:
            continue
        try:
            subtitle = (f"{result.algorithm}, карточек {result.n_cards}, "
                        f"событий {len(result.events)}")
            png = render_chart(result.curve, result.events,
                               title=result.level_key, subtitle=subtitle)
            save_debug_chart(png, category=result.category, level=result.level,
                             level_key=result.level_key, algorithm=result.algorithm,
                             grid_point_at=grid_point_at)
            saved += 1
        except Exception:
            logger.exception("Не удалось построить график %s/%s",
                             result.level_key, result.algorithm)
    logger.info("Отладочных графиков сохранено: %d (%s)", saved, policies.DEBUG_DIR)