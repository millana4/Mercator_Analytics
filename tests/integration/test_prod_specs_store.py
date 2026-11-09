# ---------- Рекомендации по закупкам: запись и чтение анализов страт (настоящий Postgres) ----------
from uuid import uuid4

from app.db.models import ProdSpecsAnalysis
from app.prod_specs.store import (delete_not_in_sets, find_source, get_analysis, save_analysis,
                                  set_label)
from sqlalchemy import func, select


def record(stratum, set_id, h="hash-1", version="v1", copied=None) -> dict:
    return {"set_id": set_id, "marketplace": "ozon", "category": "kids_clothing", "geo": "СПб",
            "stratum_id": stratum, "stratum_label": "девочки, Худи", "selection_hash": h,
            "skus": ["1", "2"], "n_cards": 2, "model": "qwen", "copied_from_stratum": copied,
            "result": {"prompt_version": version, "groups": [{"title": "Худи"}]}}


def test_save_and_read(db_session):
    stratum, set_id = str(uuid4()), str(uuid4())

    save_analysis(db_session, record(stratum, set_id))

    row = get_analysis(db_session, stratum)
    assert row.selection_hash == "hash-1"
    assert row.skus == ["1", "2"] and row.n_cards == 2
    assert row.result["groups"][0]["title"] == "Худи"
    assert row.copied_from_stratum is None
    assert row.analyzed_at is not None


def test_save_replaces_analysis_of_same_stratum(db_session):
    stratum, set_id = str(uuid4()), str(uuid4())
    save_analysis(db_session, record(stratum, set_id, h="old"))

    save_analysis(db_session, record(stratum, set_id, h="new", copied=str(uuid4())))

    assert get_analysis(db_session, stratum).selection_hash == "new"
    assert get_analysis(db_session, stratum).copied_from_stratum is not None
    count = db_session.scalar(select(func.count()).select_from(ProdSpecsAnalysis).where(
        ProdSpecsAnalysis.stratum_id == get_analysis(db_session, stratum).stratum_id))
    assert count == 1


def test_find_source_matches_hash_whatever_the_prompt_version(db_session):
    set_id = str(uuid4())
    save_analysis(db_session, record(str(uuid4()), set_id, h="same", version="v0"))

    found = find_source(db_session, "same")

    assert found is not None and found.selection_hash == "same"
    assert find_source(db_session, "unknown") is None


def test_delete_not_in_sets(db_session):
    kept_set, old_set = str(uuid4()), str(uuid4())
    kept, removed = str(uuid4()), str(uuid4())
    save_analysis(db_session, record(kept, kept_set))
    save_analysis(db_session, record(removed, old_set))

    assert delete_not_in_sets(db_session, []) == 0           # активные сеты неизвестны
    assert get_analysis(db_session, removed) is not None
    deleted = delete_not_in_sets(db_session, [kept_set])

    assert deleted >= 1
    assert get_analysis(db_session, removed) is None
    assert get_analysis(db_session, kept) is not None


def test_set_label(db_session):
    stratum = str(uuid4())
    save_analysis(db_session, record(stratum, str(uuid4())))

    assert set_label(db_session, stratum, "Худи, девочки") is True
    assert get_analysis(db_session, stratum).stratum_label == "Худи, девочки"
    assert set_label(db_session, stratum, "Худи, девочки") is False
    assert set_label(db_session, str(uuid4()), "Худи, девочки") is False