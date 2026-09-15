# ---------- Запуск полного расчёта и запись результатов в базу ----------
import logging
from datetime import datetime, timedelta

import pandas as pd

from app.analytics.service import run_for_set
from app.assignments.models import Assignment
from app.assignments.readers import read_assignment
from app.config import policies
from app.config.settings import get_settings
from app.connectors.factory import get_connector
from app.db.repository import save_run
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Файл задания для каждой категории. Пока лежит на диске рядом с дампами;
# когда появится загрузка через API, задания переедут в базу.
ASSIGNMENT_FILES = {
    "clothing": "represent_set_clothes.csv",
    "kids_clothing": "represent_set_kids_clothes.csv",
}

# Параметры алгоритма, которые сохраняются вместе с расчётом
from app.analytics.algorithms import cusum, ewma, macd

PARAMS_BY_ALGORITHM = {
    ewma.ALGORITHM: ewma.PARAMS,
    macd.ALGORITHM: macd.PARAMS,
    cusum.ALGORITHM: cusum.PARAMS,
}


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


def load_assignment(category: str, data_dir: str) -> Assignment | None:
    name = ASSIGNMENT_FILES.get(category)
    if name is None:
        logger.error("Для категории %s не задан файл задания - расчёт невозможен", category)
        return None
    path = f"{data_dir}/{name}"
    try:
        with open(path, "rb") as f:
            return read_assignment(f.read(), name, category)
    except FileNotFoundError:
        logger.error("Файл задания не найден: %s", path)
        return None


def run_all(grid_point_at: datetime | None = None) -> int:
    """Полный цикл: все сеты, все уровни, все алгоритмы. Результат - в базу.
    Возвращает число сохранённых снепшотов."""
    grid_point_at = grid_point_at or current_grid_point()
    settings = get_settings()
    connector = get_connector()

    logger.info("#" * 70)
    logger.info("ЗАПУСК РАСЧЁТА за точку сетки %s (источник: %s)",
                grid_point_at, settings.connector_kind)

    sets = connector.get_sets()
    saved = 0

    for _, row in sets.iterrows():
        assignment = load_assignment(row["category"], settings.mock_data_dir)
        if assignment is None:
            continue
        try:
            results = run_for_set(connector, assignment, row)
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

    logger.info("РАСЧЁТ ЗАВЕРШЁН: снепшотов сохранено=%d", saved)
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