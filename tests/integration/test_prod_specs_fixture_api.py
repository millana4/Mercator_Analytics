
# ---------- API рекомендаций по закупкам на выгруженных анализах настоящей модели ----------
# Фикстуры - выгрузка со стенда (tests/fixtures/prod_specs): анализы, которые реально
# написала модель, и ответы настоящего API по ним. Тест проверяет, что код отдаёт то же самое.
import json
from pathlib import Path

import pytest

from app.config import policies
from app.prod_specs.analysis import FIELD_ORDER
from app.prod_specs.store import save_analysis

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prod_specs"
URL = "/api/v1/prod_specs/strata"
SAVED = ("marketplace", "category", "geo", "stratum_id", "stratum_label", "selection_hash",
         "skus", "n_cards", "model", "result", "copied_from_stratum")


def _read_jsonl(name: str) -> list[dict]:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"нет фикстуры {path} - выгрузка мока не сделана")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture(autouse=True)
def category_enabled(monkeypatch):
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("kids_clothing",))


@pytest.fixture
def recorded():
    path = FIXTURES / "api_responses.json"
    if not path.exists():
        pytest.skip(f"нет фикстуры {path} - выгрузка мока не сделана")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def stored(db_session):
    rows = _read_jsonl("prod_specs_analysis.jsonl")
    if not rows:
        pytest.skip("в фикстуре нет анализов")
    for r in rows:
        save_analysis(db_session, {"set_id": r["source_set_id"], **{k: r[k] for k in SAVED}})
    return rows


def _strip(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "analyzed_at"}


def test_list_matches_recorded_response(client, stored, recorded):
    body = client.get(URL, params=recorded["combo"]).json()

    assert body["ready"] is True
    by_id = lambda s: s["stratum_id"]  # noqa: E731
    assert sorted((_strip(s) for s in body["strata"]), key=by_id) == sorted(
        (_strip(s) for s in recorded["strata"]["strata"]), key=by_id)


def test_details_match_recorded_responses(client, stored, recorded):
    assert recorded["details"]
    for stratum_id, expected in recorded["details"].items():
        response = client.get(f"{URL}/{stratum_id}")

        assert response.status_code == 200
        assert _strip(response.json()) == _strip(expected)


def test_real_model_answers_have_expected_shape(client, stored):
    for r in stored:
        body = client.get(f"{URL}/{r['stratum_id']}").json()

        assert body["clusters"], r["stratum_label"]
        in_clusters = sum(c["n_cards"] for c in body["clusters"])
        assert in_clusters + len(body["other_cards"]) == body["n_cards"]
        for cluster in body["clusters"]:
            assert cluster["summary"] and cluster["typical"] and cluster["features"]
            assert len(cluster["cards"]) == cluster["n_cards"]
            assert all(isinstance(c["sku"], int) and c["url"].startswith("https://")
                       for c in cluster["cards"])
            known = [f["field"] for f in cluster["features"] if f["field"] in FIELD_ORDER]
            assert known == sorted(known, key=FIELD_ORDER.index)