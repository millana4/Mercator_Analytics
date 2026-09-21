from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, analytics, auth, subscriptions, cards
from app.config.logging_setup import setup_logging
from app.config.settings import get_settings
from app.scheduler import shutdown_scheduler, start_scheduler
from app.users.seed import seed_test_user

settings = get_settings()
setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_test_user()
    if settings.scheduler_enabled:
        start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="Mercator Analytics", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(subscriptions.router)
app.include_router(analytics.router)
app.include_router(cards.router)