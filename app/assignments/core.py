# ---------- Задание из ядра: квоты и флаги подкатегорий лежат в стратах сета ----------
import logging

import pandas as pd

from app.assignments.models import Assignment, StratumTask

logger = logging.getLogger(__name__)


def assignment_from_strata(strata: pd.DataFrame, category: str,
                           source_name: str) -> Assignment | None:
    """Задание сета из его страт в ядре (схема STRATA): квота анализа - analysis_quota,
    план парсинга - count, «Отслеживать подкатегорию» - tracked. Страты с нулевым
    количеством в задание не входят (их нет и в парсинге).

    None - у страт нет квот (дамп ядра снят до V18): расчёт сета невозможен."""
    work = strata[strata["count"].fillna(0) > 0]
    if work["analysis_quota"].isna().any():
        logger.error("Задание %s: у %d страт нет квоты анализа (analysis_quota) - "
                     "расчёт сета невозможен", source_name, int(work["analysis_quota"].isna().sum()))
        return None

    tasks = []
    for _, r in work.iterrows():
        tracked = r["tracked"]
        tasks.append(StratumTask(
            gender=str(r["gender"]),
            layer=int(r["layer"]),
            subcategory=str(r["subcategory"]),
            item=str(r["item"]),
            query=str(r["query"]),
            quota_category=int(r["analysis_quota"]),
            plan_parsing=int(r["count"]),
            track_subcategory=bool(tracked) if pd.notna(tracked) else False,
            is_seasonal=bool(r["is_seasonal"]) if pd.notna(r["is_seasonal"]) else False,
            base_share=None if pd.isna(r["base_share"]) else float(r["base_share"]),
        ))
    return Assignment(category=category, source_name=source_name, strata=tasks)