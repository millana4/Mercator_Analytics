# ---------- Запись результатов расчёта ----------
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from uuid import UUID

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AnalysisEvent, AnalysisRun, MarketCurvePoint

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
        run.events.append(AnalysisEvent(
            direction=e["direction"],
            start_at=e["start_at"], end_at=e["end_at"], peak_at=e["peak_at"],
            duration_days=float(e["duration_days"]),
            # блок, упирающийся в последнюю точку данных, мог не завершиться
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
        ))

    for _, p in curve.iterrows():
        run.curve_points.append(MarketCurvePoint(
            parsed_at=p["parsed_at"], level_value=_f(p["level_value"])))

    return run


def _f(value) -> float | None:
    return None if pd.isna(value) else float(value)