import clickhouse_connect
from fastapi import APIRouter
from sqlalchemy import create_engine, text

from app.config.policies import GRID_STEPS_PER_DAY, analytics_run_times, grid_times
from app.config.settings import get_settings
from app.db.session import engine as analytics_engine

router = APIRouter(tags=["health"])
settings = get_settings()


def _check_postgres(target_engine) -> dict:
    try:
        with target_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
        "connector": settings.connector_kind,
        "grid_times": [t.strftime("%H:%M") for t in grid_times()],
        "grid_steps_per_day": GRID_STEPS_PER_DAY,
        "analytics_run_times": [t.strftime("%H:%M") for t in analytics_run_times()],
    }


@router.get("/health/deps")
def health_deps() -> dict:
    result = {"analytics_postgres": _check_postgres(analytics_engine)}

    core_engine = create_engine(settings.core_dsn, pool_pre_ping=True)
    result["core_postgres"] = _check_postgres(core_engine)
    core_engine.dispose()

    try:
        client = clickhouse_connect.get_client(
            host=settings.core_ch_host, port=settings.core_ch_port,
            username=settings.core_ch_user, password=settings.core_ch_password,
            database=settings.core_ch_db,
        )
        rows = client.query("SELECT count() FROM measurement").result_rows
        result["core_clickhouse"] = {"ok": True, "measurement_rows": rows[0][0]}
    except Exception as exc:
        result["core_clickhouse"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    result["all_ok"] = all(v.get("ok") for v in result.values() if isinstance(v, dict))
    return result