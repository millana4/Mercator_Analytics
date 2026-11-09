# ---------- Планировщик: расчёт после каждой точки сетки, досылка ниш в ядро ----------
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.analytics.runner import run_all
from app.config.policies import (NICHE_RESEND_INTERVAL_SECONDS, analytics_run_times,
                                 cron_expression)
from app.config import policies
from app.niches.service import resend_to_core
from app.prod_specs.runner import run_prod_specs

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    hours, minutes = cron_expression()
    scheduler = BackgroundScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        run_all, CronTrigger(hour=hours, minute=minutes, timezone="Europe/Moscow"),
        id="analytics_run", replace_existing=True,
        max_instances=1,           # расчёт не должен накладываться сам на себя
        misfire_grace_time=3600,   # пропущенный запуск отработает в течение часа
    )
    scheduler.add_job(
        resend_to_core, IntervalTrigger(seconds=NICHE_RESEND_INTERVAL_SECONDS),
        id="niche_resend", replace_existing=True,
        max_instances=1,           # досылка не накладывается сама на себя
        coalesce=True,             # пропущенные запуски схлопываются в один
    )
    hour, minute = policies.PROD_SPECS_RUN_TIME
    scheduler.add_job(
        run_prod_specs,
        CronTrigger(day_of_week=policies.PROD_SPECS_RUN_DAY, hour=hour, minute=minute,
                    timezone="Europe/Moscow"),
        id="prod_specs_run", replace_existing=True,
        max_instances=1,           # запуск ждёт разметку до нескольких часов и не накладывается
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()

    times = ", ".join(t.strftime("%H:%M") for t in analytics_run_times())
    logger.info("Планировщик запущен, расчёт в %s, досылка ниш раз в %s с",
                times, NICHE_RESEND_INTERVAL_SECONDS)
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None