# ---------- API рекомендаций по закупкам (настоящий Postgres, ядро не опрашивается) ----------
from uuid import uuid4

import pytest

from app.analytics import active_sets
from app.config import policies
from app.prod_specs.store import save_analysis

GEO = "Санкт-Петербург"
COMBO = {"category": "kids_clothing", "geo": GEO, "marketplace": "ozon"}
URL = "/api/v1/prod_specs/strata"


@pytest.fixture(autouse=True)
def category_enabled(monkeypatch):
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("kids_clothing",))


def freq(*pairs) -> list[dict]:
    return [{"value": v, "count": c, "share": 0.5} for v, c in pairs]


def group(title="Пижама для девочки", other=False, skus=("1", "2", "3")) -> dict:
    return {"title": title, "skus": list(skus), "n_cards": len(skus), "summary": "Хлопковая пижама.",
            "features_intro": "внутреннее",
            "description": ["Фасон: свободный", "Длина: стандартная"],
            "frequencies": {"extra": freq(("бант", 1)), "silhouette": freq(("свободный", 3)),
                            "garment_type": freq(("пижама", 3)), "decor": freq(("рюши", 2))},
            "cards": [{"sku": s, "name": f"Карточка {s}", "url": f"https://ozon/{s}"} for s in skus],
            "other": other}


def put(session, label, set_id, category="kids_clothing", geo=GEO) -> str:
    stratum = str(uuid4())
    groups = [group(), group("Прочее", other=True, skus=("4",))]
    save_analysis(session, {
        "set_id": set_id, "marketplace": "ozon", "category": category, "geo": geo,
        "stratum_id": stratum, "stratum_label": label, "selection_hash": f"hash-{stratum}",
        "skus": ["1", "2", "3", "4"], "n_cards": 4, "model": "qwen", "copied_from_stratum": None,
        "result": {"prompt_version": "v4", "grouping_note": "внутреннее", "intro": "внутреннее",
                   "frequencies": {}, "groups": groups}})
    return stratum


def test_list_returns_combo_strata_sorted_by_label(client, db_session):
    set_id = str(uuid4())
    boys = put(db_session, "мальчики, Рубашка", set_id)
    girls = put(db_session, "девочки, Пижама", set_id)
    put(db_session, "взрослая", str(uuid4()), category="clothing")
    put(db_session, "другое гео", set_id, geo="Москва")

    body = client.get(URL, params=COMBO).json()

    assert body["ready"] is True
    assert [s["label"] for s in body["strata"]] == ["девочки, Пижама", "мальчики, Рубашка"]
    assert [s["subcategory"] for s in body["strata"]] == ["девочки, Пижама", "мальчики, Рубашка"]
    assert [s["stratum_id"] for s in body["strata"]] == [girls, boys]
    assert body["strata"][0]["clusters"] == 1 and body["strata"][0]["n_cards"] == 4


def test_list_gives_subcategory_without_item(client, db_session):
    put(db_session, "Блузы и рубашки, девочки: Рубашка", str(uuid4()))
    put(db_session, "Домашняя одежда, девочки", str(uuid4()))

    body = client.get(URL, params=COMBO).json()

    assert [s["subcategory"] for s in body["strata"]] == [
        "Блузы и рубашки, девочки", "Домашняя одежда, девочки"]


def test_list_is_empty_for_combo_without_analyses(client, db_session):
    put(db_session, "девочки, Пижама", str(uuid4()))

    body = client.get(URL, params={**COMBO, "geo": "Москва"}).json()

    assert body == {"ready": False, "strata": []}


def test_list_is_empty_when_category_is_disabled(client, db_session, monkeypatch):
    put(db_session, "девочки, Пижама", str(uuid4()))
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("clothing",))

    assert client.get(URL, params=COMBO).json() == {"ready": False, "strata": []}


def test_list_follows_active_set(client, db_session, monkeypatch):
    old_set, new_set = str(uuid4()), str(uuid4())
    put(db_session, "старая", old_set)
    put(db_session, "новая", new_set)

    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a, **k: [new_set])
    assert [s["label"] for s in client.get(URL, params=COMBO).json()["strata"]] == ["новая"]

    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a, **k: [])
    assert client.get(URL, params=COMBO).json()["strata"] == []

    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a, **k: None)   # ядро недоступно
    assert [s["label"] for s in client.get(URL, params=COMBO).json()["strata"]] == ["новая", "старая"]


def test_detail_has_clusters_features_cards_and_hides_internal_fields(client, db_session):
    stratum = put(db_session, "девочки, Пижама", str(uuid4()))

    response = client.get(f"{URL}/{stratum}")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"stratum_id", "label", "category", "geo", "marketplace",
                         "analyzed_at", "n_cards", "clusters", "other_cards"}
    assert body["label"] == "девочки, Пижама" and len(body["clusters"]) == 1
    cluster = body["clusters"][0]
    assert set(cluster) == {"title", "n_cards", "summary", "typical", "features", "cards"}
    assert cluster["title"] == "Пижама для девочки"
    assert cluster["typical"] == ["Фасон: свободный", "Длина: стандартная"]
    assert [f["field"] for f in cluster["features"]] == ["garment_type", "silhouette", "decor", "extra"]
    assert cluster["features"][1]["values"] == [{"value": "свободный", "count": 3}]
    assert cluster["cards"][0] == {"sku": 1, "name": "Карточка 1", "url": "https://ozon/1"}
    assert body["other_cards"] == [{"sku": 4, "name": "Карточка 4", "url": "https://ozon/4"}]


def test_detail_not_found(client, db_session):
    assert client.get(f"{URL}/{uuid4()}").status_code == 404


def test_detail_hidden_for_inactive_set(client, db_session, monkeypatch):
    set_id = str(uuid4())
    stratum = put(db_session, "девочки, Пижама", set_id)

    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a, **k: [str(uuid4())])
    assert client.get(f"{URL}/{stratum}").status_code == 404

    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a, **k: [set_id])
    assert client.get(f"{URL}/{stratum}").status_code == 200

    monkeypatch.setattr(active_sets, "active_set_ids", lambda *a, **k: None)
    assert client.get(f"{URL}/{stratum}").status_code == 200


def test_detail_hidden_for_disabled_category(client, db_session, monkeypatch):
    stratum = put(db_session, "девочки, Пижама", str(uuid4()))
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("clothing",))

    assert client.get(f"{URL}/{stratum}").status_code == 404