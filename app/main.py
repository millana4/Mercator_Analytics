from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, analytics
from app.config.logging_setup import setup_logging
from app.config.settings import get_settings
from app.scheduler import shutdown_scheduler, start_scheduler

settings = get_settings()
setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.scheduler_enabled:
        start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="Mercator Analytics", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(analytics.router)