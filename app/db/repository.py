# ---------- Запись результатов расчёта ----------
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from uuid import UUID

import pandas as pd
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (AnalysisEvent, AnalysisRun, AnalysisEventParticipant,
                           MarketCurvePoint, SearchPoint)

logger = logging.getLogger(__name__)


def _params_json(params) -> str | None:
    if params is None:
        return None
    data = asdict(params) if is_dataclass(params) else dict(params)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def save_run(session: Session, result, *, marketplace: str, geo: str,
             grid_point_at: datetime, params=None) -> AnalysisRun:
    """Сохраняет снепшот. Повторный запуск за ту же точку сетки заменяет
    прежнюю запись - дубли не копятся.

    События хранятся навсегда: это и есть история для изучения алгоритмов.
    Кривая рынка - только у последнего снепшота комбинации: старые копии
    не несут информации, которой нет в свежей, а растут линейно со временем.
    """
    slice_filter = (
        AnalysisRun.source_set_id == UUID(str(result.set_id)),
        AnalysisRun.level == result.level,
        AnalysisRun.level_key == result.level_key,
        AnalysisRun.algorithm == result.algorithm,
    )
    # 1. Тот же снепшот за ту же точку сетки - пересчёт, старую запись убираем целиком
    existing = session.scalar(select(AnalysisRun).where(
        *slice_filter, AnalysisRun.grid_point_at == grid_point_at))
    if existing is not None:
        session.execute(delete(AnalysisRun).where(AnalysisRun.id == existing.id))
        session.flush()

    # 2. У прежних снепшотов этой комбинации чистим кривую, сами снепшоты и события остаются
    stale_ids = session.scalars(select(AnalysisRun.id).where(*slice_filter)).all()
    if stale_ids:
        session.execute(delete(MarketCurvePoint).where(
            MarketCurvePoint.run_id.in_(stale_ids)))
        session.flush()

    curve = result.curve if isinstance(result.curve, pd.DataFrame) else pd.DataFrame()
    run = AnalysisRun(
        source_set_id=UUID(str(result.set_id)),
        marketplace=marketplace, category=result.category, geo=geo,
        level=result.level, level_key=result.level_key, algorithm=result.algorithm,
        grid_point_at=grid_point_at,
        data_from=curve["parsed_at"].min() if len(curve) else None,
        data_to=curve["parsed_at"].max() if len(curve) else None,
        n_cards=result.n_cards, total_quota=result.total_quota,
        deficit_share=result.deficit_share, scale_factor=result.scale_factor,
        sufficient=result.sufficient, insufficient_reason=result.insufficient_reason,
        warning=result.warning, params_json=_params_json(params),
    )
    session.add(run)
    session.flush()

    if not result.sufficient:
        return run

    last_point = curve["parsed_at"].max() if len(curve) else None
    for _, e in result.events.iterrows():
        event = AnalysisEvent(
            direction=e["direction"],
            start_at=e["start_at"], end_at=e["end_at"], peak_at=e["peak_at"],
            duration_days=float(e["duration_days"]),
            is_open=bool(last_point is not None and e["end_at"] >= last_point),
            participation_pct=float(e["participation_pct"]),
            participation_ci_low=_f(e["participation_ci_low"]),
            participation_ci_high=_f(e["participation_ci_high"]),
            magnitude_mean_log=_f(e["magnitude_mean_log"]),
            magnitude_peak_log=_f(e["magnitude_peak_log"]),
            magnitude_ci_low_log=_f(e["magnitude_ci_low_log"]),
            magnitude_ci_high_log=_f(e["magnitude_ci_high_log"]),
            speed_mean_log=_f(e["speed_mean_log"]),
            speed_peak_log=_f(e["speed_peak_log"]),
            speed_ci_low_log=_f(e["speed_ci_low_log"]),
            speed_ci_high_log=_f(e["speed_ci_high_log"]),
        )
        for p in e.get("participants", []) or []:
            event.participants.append(AnalysisEventParticipant(
                card_id=UUID(str(p["card_id"])), sku=int(p["sku"]),
                name=p["name"], url=p["url"],
            ))
        run.events.append(event)

    for _, p in curve.iterrows():
        run.curve_points.append(MarketCurvePoint(
            parsed_at=p["parsed_at"], level_value=_f(p["level_value"])))

    return run


def _f(value) -> float | None:
    return None if pd.isna(value) else float(value)


# ---------- Анализ поисковой выдачи ----------
def save_search_points(session: Session, points: pd.DataFrame, *, set_id: str,
                       marketplace: str, category: str, geo: str) -> int:
    """Ряд сета пересчитывается целиком: прежние точки сета удаляются, пишутся новые.
    points - индикатор (search_dynamics.INDICATOR_COLUMNS) + погода + strata_total."""
    session.execute(delete(SearchPoint).where(SearchPoint.source_set_id == UUID(str(set_id))))
    for p in points.itertuples():
        session.add(SearchPoint(
            source_set_id=UUID(str(set_id)), marketplace=marketplace, category=category, geo=geo,
            run_at=p.run_at, prev_run=p.prev_run,
            strata_n=int(p.strata_n), strata_total=int(p.strata_total),
            repeat_pct=_f(p.repeat_pct), normal_pct=_f(p.normal_pct),
            band_low=_f(p.band_low), band_high=_f(p.band_high), z=_f(p.z), status=p.status,
            temp_mean=_f(p.temp_mean), temp_now=_f(p.temp_now), temp_min=_f(p.temp_min),
            temp_max=_f(p.temp_max), precip=_f(p.precip),
        ))
    session.flush()
    return len(points)


def list_search_points(session: Session, *, category: str, geo: str,
                       marketplace: str, windows: list | None = None) -> list[dict]:
    """Точки ряда связки по времени, все статусы - отбор показываемых делает API.

    windows - склейка ряда по сменам сезонного сета: [(set_id, с, до или None)];
    точка сета берётся, только если её снимок попал в окно сета. None - все точки связки."""
    q = select(SearchPoint).where(SearchPoint.category == category, SearchPoint.geo == geo,
                                  SearchPoint.marketplace == marketplace)
    if windows is not None:
        if not windows:
            return []
        conditions = []
        for set_id, start, end in windows:
            cond = and_(SearchPoint.source_set_id == UUID(str(set_id)), SearchPoint.run_at >= start)
            if end is not None:
                cond = and_(cond, SearchPoint.run_at < end)
            conditions.append(cond)
        q = q.where(or_(*conditions))
    rows = session.scalars(q.order_by(SearchPoint.run_at)).all()
    return [{"run_at": r.run_at, "prev_run": r.prev_run, "strata_n": r.strata_n,
             "strata_total": r.strata_total, "repeat_pct": r.repeat_pct,
             "normal_pct": r.normal_pct, "band_low": r.band_low, "band_high": r.band_high,
             "z": r.z, "status": r.status, "temp_mean": r.temp_mean, "temp_now": r.temp_now,
             "temp_min": r.temp_min, "temp_max": r.temp_max, "precip": r.precip}
            for r in rows]


# ---------- Чтение результатов для API ----------
def list_catalog(session: Session) -> list[dict]:
    """Доступные сочетания категория + гео + маркетплейс - для выбора на фронте."""
    rows = session.execute(
        select(AnalysisRun.category, AnalysisRun.geo, AnalysisRun.marketplace)
        .distinct().order_by(AnalysisRun.category, AnalysisRun.geo)
    ).all()
    return [{"category": c, "geo": g, "marketplace": m} for c, g, m in rows]


def _in_sets(set_ids: list[str] | None) -> tuple:
    """Условие «расчёт по одному из сетов» для фильтра по активному сету; None - без фильтра."""
    if set_ids is None:
        return ()
    return (AnalysisRun.source_set_id.in_([UUID(str(s)) for s in set_ids]),)


def latest_run(session: Session, *, category: str, geo: str, marketplace: str,
               level: str, level_key: str, algorithm: str,
               set_ids: list[str] | None = None) -> AnalysisRun | None:
    """Самый свежий снепшот по точке сетки. set_ids - только расчёты этих сетов
    (активный сет связки: сет в сборе тоже считается, но не показывается)."""
    return session.scalar(
        select(AnalysisRun).where(
            AnalysisRun.category == category, AnalysisRun.geo == geo,
            AnalysisRun.marketplace == marketplace, AnalysisRun.level == level,
            AnalysisRun.level_key == level_key, AnalysisRun.algorithm == algorithm,
            *_in_sets(set_ids),
        ).order_by(AnalysisRun.grid_point_at.desc()).limit(1)
    )


def list_levels(session: Session, *, category: str, geo: str, marketplace: str,
                level: str, set_ids: list[str] | None = None) -> list[dict]:
    """Какие снепшоты этого уровня посчитаны и по каким алгоритмам есть результат.
    Только действующие алгоритмы: старые расчёты снятых (CUSUM) остаются в базе,
    но не влияют на список и на готовность ниш. set_ids - только расчёты этих сетов."""
    from app.analytics.service import ALGORITHMS
    active = [module.ALGORITHM for module in ALGORITHMS]

    subq = (select(AnalysisRun.level_key, AnalysisRun.algorithm,
                   func.max(AnalysisRun.grid_point_at).label("last_point"))
            .where(AnalysisRun.category == category, AnalysisRun.geo == geo,
                   AnalysisRun.marketplace == marketplace, AnalysisRun.level == level,
                   AnalysisRun.algorithm.in_(active), *_in_sets(set_ids))
            .group_by(AnalysisRun.level_key, AnalysisRun.algorithm).subquery())

    rows = session.execute(
        select(AnalysisRun.level_key, AnalysisRun.algorithm, AnalysisRun.sufficient,
               AnalysisRun.n_cards, AnalysisRun.insufficient_reason,
               AnalysisRun.grid_point_at)
        .join(subq, (AnalysisRun.level_key == subq.c.level_key)
              & (AnalysisRun.algorithm == subq.c.algorithm)
              & (AnalysisRun.grid_point_at == subq.c.last_point))
        .where(AnalysisRun.category == category, AnalysisRun.geo == geo,
               AnalysisRun.marketplace == marketplace, AnalysisRun.level == level,
               AnalysisRun.algorithm.in_(active), *_in_sets(set_ids))
        .order_by(AnalysisRun.level_key, AnalysisRun.algorithm)
    ).all()

    return [{"level_key": k, "algorithm": a, "sufficient": s, "n_cards": n,
             "insufficient_reason": r, "grid_point_at": g}
            for k, a, s, n, r, g in rows]


def run_events(session: Session, run: AnalysisRun) -> pd.DataFrame:
    rows = session.scalars(
        select(AnalysisEvent).where(AnalysisEvent.run_id == run.id)
        .order_by(AnalysisEvent.start_at)).all()
    if not rows:
        return pd.DataFrame(columns=["id", "direction", "start_at", "end_at", "peak_at"])
    return pd.DataFrame([{
        "id": str(e.id),
        "direction": e.direction, "start_at": e.start_at, "end_at": e.end_at,
        "peak_at": e.peak_at, "duration_days": e.duration_days, "is_open": e.is_open,
        "participation_pct": e.participation_pct,
        "participation_ci_low": e.participation_ci_low,
        "participation_ci_high": e.participation_ci_high,
        "magnitude_mean_log": e.magnitude_mean_log,
        "magnitude_peak_log": e.magnitude_peak_log,
        "magnitude_ci_low_log": e.magnitude_ci_low_log,
        "magnitude_ci_high_log": e.magnitude_ci_high_log,
        "speed_mean_log": e.speed_mean_log, "speed_peak_log": e.speed_peak_log,
        "speed_ci_low_log": e.speed_ci_low_log, "speed_ci_high_log": e.speed_ci_high_log,
    } for e in rows])


def run_curve(session: Session, run: AnalysisRun) -> pd.DataFrame:
    rows = session.scalars(
        select(MarketCurvePoint).where(MarketCurvePoint.run_id == run.id)
        .order_by(MarketCurvePoint.parsed_at)).all()
    if not rows:
        return pd.DataFrame(columns=["parsed_at", "level_value"])
    return pd.DataFrame([{"parsed_at": p.parsed_at, "level_value": p.level_value}
                         for p in rows])


def list_event_participants(session: Session, event_id: str) -> list[dict]:
    rows = session.execute(
        select(AnalysisEventParticipant)
        .where(AnalysisEventParticipant.event_id == UUID(str(event_id)))
        .order_by(AnalysisEventParticipant.name)
    ).scalars().all()
    return [{"card_id": str(r.card_id), "sku": r.sku, "name": r.name, "url": r.url}
            for r in rows]


# ---------- Готовность сета (для ядра: переключение на новый сезонный сет) ----------
def set_readiness(session: Session, set_id: str) -> dict:
    """Последний расчёт уровня категории по каждому действующему алгоритму:
    {алгоритм: {sufficient, n_cards, grid_point_at, insufficient_reason} или None}."""
    from app.analytics.service import ALGORITHMS
    source = UUID(str(set_id))
    out: dict = {}
    for module in ALGORITHMS:
        run = session.scalar(
            select(AnalysisRun).where(
                AnalysisRun.source_set_id == source, AnalysisRun.level == "category",
                AnalysisRun.algorithm == module.ALGORITHM,
            ).order_by(AnalysisRun.grid_point_at.desc()).limit(1))
        out[module.ALGORITHM] = None if run is None else {
            "sufficient": run.sufficient, "n_cards": run.n_cards,
            "grid_point_at": run.grid_point_at, "insufficient_reason": run.insufficient_reason,
        }
    return out