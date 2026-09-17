# ---------- Эндпоинты аналитики ----------
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.analytics.algorithms.common import format_events, log_to_pct
from app.db import repository as repo
from app.db.session import get_session
from app.viz.charts import render_chart

router = APIRouter(prefix="/api/v1", tags=["analytics"])


@router.get("/catalog")
def catalog(session: Session = Depends(get_session)) -> list[dict]:
    """Доступные сочетания категория + гео + маркетплейс."""
    return repo.list_catalog(session)


@router.get("/levels")
def levels(category: str, geo: str, marketplace: str,
           level: str = Query("category", pattern="^(category|subcategory|item)$"),
           session: Session = Depends(get_session)) -> list[dict]:
    """Какие снепшоты этого уровня посчитаны и где данных не хватило."""
    return repo.list_levels(session, category=category, geo=geo,
                            marketplace=marketplace, level=level)


@router.get("/snapshot")
def snapshot(category: str, geo: str, marketplace: str, level_key: str,
             algorithm: str = Query(..., pattern="^(EWMA|MACD|CUSUM)$"),
             level: str = Query("category", pattern="^(category|subcategory|item)$"),
             session: Session = Depends(get_session)) -> dict:
    """Снепшот целиком: метаданные, события и кривая рынка.

    Величина и скорость отдаются и в лог-единицах (для расчётов),
    и в процентах (для отображения) - чтобы фронт не занимался пересчётом.
    """
    run = repo.latest_run(session, category=category, geo=geo, marketplace=marketplace,
                          level=level, level_key=level_key, algorithm=algorithm)
    if run is None:
        raise HTTPException(404, "Снепшот не найден")

    meta = {
        "category": run.category, "geo": run.geo, "marketplace": run.marketplace,
        "level": run.level, "level_key": run.level_key, "algorithm": run.algorithm,
        "grid_point_at": run.grid_point_at, "computed_at": run.computed_at,
        "data_from": run.data_from, "data_to": run.data_to,
        "n_cards": run.n_cards, "total_quota": run.total_quota,
        "deficit_share": run.deficit_share, "scale_factor": run.scale_factor,
        "sufficient": run.sufficient, "insufficient_reason": run.insufficient_reason,
        "warning": run.warning,
    }
    if not run.sufficient:
        return {"meta": meta, "events": [], "curve": []}

    events = repo.run_events(session, run)
    curve = repo.run_curve(session, run)

    events_out = []
    for _, e in events.iterrows():
        events_out.append({
            "direction": e["direction"],
            "start_at": e["start_at"], "end_at": e["end_at"], "peak_at": e["peak_at"],
            "duration_days": e["duration_days"], "is_open": bool(e["is_open"]),
            "participation_pct": e["participation_pct"] * 100,
            "participation_ci": [_pct(e["participation_ci_low"]),
                                 _pct(e["participation_ci_high"])],
            "magnitude_pct": _log_pct(e["magnitude_mean_log"]),
            "magnitude_ci_pct": [_log_pct(e["magnitude_ci_low_log"]),
                                 _log_pct(e["magnitude_ci_high_log"])],
            "speed_pct_per_day": _log_pct(e["speed_mean_log"]),
            "speed_ci_pct_per_day": [_log_pct(e["speed_ci_low_log"]),
                                     _log_pct(e["speed_ci_high_log"])],
        })

    curve_out = [{"parsed_at": r["parsed_at"], "level_log": r["level_value"]}
                 for _, r in curve.iterrows()]
    return {"meta": meta, "events": events_out, "curve": curve_out}


@router.get("/chart.png")
def chart(category: str, geo: str, marketplace: str, level_key: str,
          algorithm: str = Query(..., pattern="^(EWMA|MACD|CUSUM)$"),
          level: str = Query("category", pattern="^(category|subcategory|item)$"),
          session: Session = Depends(get_session)) -> Response:
    """PNG для отчёта и отладки. Основной формат для фронта - /snapshot."""
    run = repo.latest_run(session, category=category, geo=geo, marketplace=marketplace,
                          level=level, level_key=level_key, algorithm=algorithm)
    if run is None:
        raise HTTPException(404, "Снепшот не найден")

    curve = repo.run_curve(session, run)
    events = repo.run_events(session, run)
    subtitle = f"{run.algorithm}, карточек {run.n_cards}, событий {len(events)}"
    png = render_chart(curve, events, title=run.level_key, subtitle=subtitle)
    return Response(content=png, media_type="image/png")


def _pct(value) -> float | None:
    return None if value is None else value * 100


def _log_pct(value) -> float | None:
    return None if value is None else log_to_pct(value)


@router.get("/events/{event_id}/participants")
def event_participants(event_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Карточки, участвовавшие в конкретном событии."""
    return repo.list_event_participants(session, event_id)