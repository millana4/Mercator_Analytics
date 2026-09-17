# ---------- save_run: замена снепшота, чистка кривой, участники, list_* ----------
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import func

import pandas as pd
import pytest

from app.db.repository import list_catalog, list_event_participants, list_levels, save_run


def _make_result(*, set_id, category="clothing", level="category", level_key="clothing",
                 algorithm="EWMA", sufficient=True, n_cards=50, events=None, curve=None):
    return SimpleNamespace(
        set_id=set_id, category=category, level=level, level_key=level_key,
        algorithm=algorithm, n_cards=n_cards, total_quota=100,
        deficit_share=0.0, scale_factor=1.0, sufficient=sufficient,
        insufficient_reason=None, warning=None,
        events=events if events is not None else pd.DataFrame(),
        curve=curve if curve is not None else pd.DataFrame(),
    )


def _empty_event_fields(direction="down", start=None, end=None, peak=None, participants=None):
    start = start or pd.Timestamp("2026-08-01")
    end = end or pd.Timestamp("2026-08-05")
    return {
        "direction": direction, "start_at": start, "end_at": end, "peak_at": peak or start,
        "duration_days": (end - start).total_seconds() / 86400,
        "participation_pct": 0.5, "participation_ci_low": 0.4, "participation_ci_high": 0.6,
        "magnitude_mean_log": -0.1, "magnitude_peak_log": -0.12,
        "magnitude_ci_low_log": -0.15, "magnitude_ci_high_log": -0.08,
        "speed_mean_log": -0.02, "speed_peak_log": -0.03,
        "speed_ci_low_log": -0.04, "speed_ci_high_log": -0.01,
        "participants": participants or [],
    }


def test_save_run_insufficient_has_no_events(db_session):
    result = _make_result(set_id=uuid4(), sufficient=False, n_cards=0)
    run = save_run(db_session, result, marketplace="ozon", geo="spb",
                   grid_point_at=pd.Timestamp("2026-09-01"))
    db_session.commit()
    assert run.sufficient is False
    assert len(run.events) == 0


def test_save_run_same_grid_point_replaces_not_duplicates(db_session):
    set_id = uuid4()
    grid_point = pd.Timestamp("2026-09-01")
    curve = pd.DataFrame({"parsed_at": [pd.Timestamp("2026-08-01")], "level_value": [1.0]})

    save_run(db_session, _make_result(set_id=set_id, n_cards=10, curve=curve),
             marketplace="ozon", geo="spb", grid_point_at=grid_point)
    db_session.commit()
    save_run(db_session, _make_result(set_id=set_id, n_cards=20, curve=curve),
             marketplace="ozon", geo="spb", grid_point_at=grid_point)
    db_session.commit()

    runs = list_levels(db_session, category="clothing", geo="spb",
                       marketplace="ozon", level="category")
    matching = [r for r in runs if r["level_key"] == "clothing" and r["algorithm"] == "EWMA"]
    assert len(matching) == 1
    assert matching[0]["n_cards"] == 20  # осталась вторая запись, не первая


def test_save_run_keeps_event_history_across_grid_points(db_session):
    set_id = uuid4()
    events = pd.DataFrame([_empty_event_fields()])

    save_run(db_session, _make_result(set_id=set_id, events=events),
             marketplace="ozon", geo="spb", grid_point_at=pd.Timestamp("2026-09-01"))
    db_session.commit()
    save_run(db_session, _make_result(set_id=set_id, events=events),
             marketplace="ozon", geo="spb", grid_point_at=pd.Timestamp("2026-09-02"))
    db_session.commit()

    from sqlalchemy import select
    from app.db.models import AnalysisEvent, AnalysisRun

    count = db_session.scalar(
        select(func.count()).select_from(AnalysisEvent)
        .join(AnalysisRun).where(AnalysisRun.source_set_id == set_id)
    )
    assert count == 2  # события от обоих расчётов сохранились, не заменили друг друга


def test_save_run_clears_old_curve_keeps_new(db_session):
    set_id = uuid4()
    old_curve = pd.DataFrame({"parsed_at": [pd.Timestamp("2026-08-01")], "level_value": [1.0]})
    new_curve = pd.DataFrame({"parsed_at": [pd.Timestamp("2026-08-01"), pd.Timestamp("2026-08-02")],
                             "level_value": [1.0, 1.1]})

    save_run(db_session, _make_result(set_id=set_id, curve=old_curve),
             marketplace="ozon", geo="spb", grid_point_at=pd.Timestamp("2026-09-01"))
    db_session.commit()
    save_run(db_session, _make_result(set_id=set_id, curve=new_curve),
             marketplace="ozon", geo="spb", grid_point_at=pd.Timestamp("2026-09-02"))
    db_session.commit()

    from sqlalchemy import select
    from app.db.models import AnalysisRun, MarketCurvePoint

    old_run = db_session.scalar(select(AnalysisRun).where(
        AnalysisRun.source_set_id == set_id,
        AnalysisRun.grid_point_at == pd.Timestamp("2026-09-01")))
    points = db_session.scalars(select(MarketCurvePoint)
                                .where(MarketCurvePoint.run_id == old_run.id)).all()
    assert len(points) == 0  # у старого снепшота кривую почистили


def test_save_run_writes_participants(db_session):
    set_id = uuid4()
    participants = [{"card_id": str(uuid4()), "sku": 111, "name": "Товар", "url": "https://x/1"}]
    events = pd.DataFrame([_empty_event_fields(participants=participants)])

    save_run(db_session, _make_result(set_id=set_id, events=events),
             marketplace="ozon", geo="spb", grid_point_at=pd.Timestamp("2026-09-01"))
    db_session.commit()

    from sqlalchemy import select
    from app.db.models import AnalysisEvent

    event = db_session.scalar(select(AnalysisEvent))
    result = list_event_participants(db_session, str(event.id))
    assert len(result) == 1
    assert result[0]["sku"] == 111
    assert result[0]["name"] == "Товар"


def test_list_catalog_returns_distinct_combos(db_session):
    save_run(db_session, _make_result(set_id=uuid4(), n_cards=10),
             marketplace="ozon", geo="spb", grid_point_at=pd.Timestamp("2026-09-01"))
    db_session.commit()
    catalog = list_catalog(db_session)
    assert {"category": "clothing", "geo": "spb", "marketplace": "ozon"} in catalog