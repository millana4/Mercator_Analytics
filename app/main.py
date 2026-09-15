from fastapi import FastAPI

from app.api import health
from app.config.logging_setup import setup_logging
from app.config.settings import get_settings

settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(title="Mercator Analytics", version="0.1.0")
app.include_router(health.router)