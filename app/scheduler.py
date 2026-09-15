# ---------- Планировщик: запуск расчёта после каждой точки сетки ----------
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.analytics.runner import run_all
from app.config.policies import analytics_run_times, cron_expression

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    hours, minutes = cron_expression()
    scheduler = BackgroundScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        run_all, CronTrigger(hour=hours, minute=minutes),
        id="analytics_run", replace_existing=True,
        max_instances=1,           # расчёт не должен накладываться сам на себя
        misfire_grace_time=3600,   # пропущенный запуск отработает в течение часа
    )
    scheduler.start()

    times = ", ".join(t.strftime("%H:%M") for t in analytics_run_times())
    logger.info("Планировщик запущен, расчёт в %s", times)
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None