# ---------- Задание детской одежды из страт ядра (V18) ----------
from pathlib import Path

import pandas as pd
import pytest

from app.assignments.core import assignment_from_strata
from app.assignments.readers import read_assignment
from app.connectors.mock import MockConnector
from tests.data_files import find_latest_pg_dump

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "backups"
KIDS_CSV = DATA_DIR / "represent_set_kids_clothes.csv"


def _strata(quotas) -> pd.DataFrame:
    """Две подкатегории летнего сета: верхняя одежда (отслеживается) и нижнее бельё
    с бюстгальтером из нуля карточек."""
    return pd.DataFrame({
        "stratum_id": ["a", "b", "c", "d"],
        "gender": ["f", "f", "f", "f"],
        "layer": pd.array([3, 3, 0, 0], dtype="Int64"),
        "subcategory": ["Верхняя одежда", "Верхняя одежда", "Нижнее белье", "Нижнее белье"],
        "item": ["Куртка", "Пуховик", "Трусы", "Бюстгальтер"],
        "query": ["Куртка для девочки", "Пуховик для девочки", "Трусы для девочки",
                  "Бюстгальтер для девочки"],
        "count": pd.array([29, 12, 2, 0], dtype="Int64"),
        "analysis_quota": pd.array(quotas, dtype="Int64"),
        "tracked": pd.array([True, True, False, False], dtype="boolean"),
        "is_seasonal": pd.array([False, False, False, False], dtype="boolean"),
        "base_share": pd.array([None, None, None, None], dtype="Float64"),
    })


def test_quotas_plan_and_tracked_from_core():
    a = assignment_from_strata(_strata([7, 3, 1, 0]), "kids_clothing", "тест")
    assert [s.item for s in a.strata] == ["Куртка", "Пуховик", "Трусы"]   # нулевая страта не входит
    assert a.total_quota == 11
    assert a.total_plan == 43
    assert a.tracked_subcategories() == [("f", 3, "Верхняя одежда")]


def test_missing_quota_gives_none():
    # дамп до V18: квот нет - расчёт сета невозможен
    assert assignment_from_strata(_strata([None, None, None, None]), "kids_clothing", "тест") is None


def test_core_quotas_match_csv_assignment():
    """Квоты, план и «Отслеживать» из ядра совпадают с прежним заданием из CSV
    по каждой страте активного сета детской одежды (дамп после V18)."""
    if not KIDS_CSV.exists():
        pytest.skip(f"Нет файла задания {KIDS_CSV}")
    conn = MockConnector(DATA_DIR)
    sets = conn.get_sets()
    kids = sets[(sets["category"] == "kids_clothing") & (sets["status"] == "active")]
    if kids.empty:
        pytest.skip("В дампе нет активного сета детской одежды")
    strata = conn.get_strata(kids.iloc[0]["set_id"])
    core = assignment_from_strata(strata, "kids_clothing", find_latest_pg_dump(DATA_DIR).name)
    if core is None:
        pytest.skip("Дамп ядра снят до V18 - квот в стратах нет")
    csv = read_assignment(KIDS_CSV.read_bytes(), KIDS_CSV.name, "kids_clothing")

    def by_key(assignment):
        return {(s.gender, s.layer, s.subcategory, s.item):
                (s.quota_category, s.plan_parsing, s.track_subcategory)
                for s in assignment.strata}

    assert by_key(core) == by_key(csv)