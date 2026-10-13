# ---------- API ниш: создание, проверки, лимит, удаление, дашборд, снепшот уровня niche ----------
from types import SimpleNamespace
from uuid import UUID, uuid4

import pandas as pd
import pytest

from app.config import policies
from app.db.repository import save_run
from app.niches import service
from app.niches.models import Niche

KIDS = {"category": "kids_clothing", "marketplace": "ozon", "geo": "Санкт-Петербург"}
REF_URL = "https://www.ozon.ru/product/platye-dlya-devochki-123456789/"


class FakeCoreClient:
    """Ядро, которое принимает создание и удаление ниш."""

    def __init__(self):
        self.created, self.deleted = [], []

    def create_niche(self, niche):
        self.created.append(niche.id)

    def delete_niche(self, niche_id):
        self.deleted.append(niche_id)


@pytest.fixture(autouse=True)
def _policies(monkeypatch):
    """Настройки из кода фиксируем, чтобы тесты не зависели от policies.py."""
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", "all")
    monkeypatch.setattr(policies, "FREE_NICHES_INTERNAL", 2)
    monkeypatch.setattr(policies, "FREE_NICHES_EXTERNAL", 2)


@pytest.fixture
def core_client(monkeypatch):
    fake = FakeCoreClient()
    monkeypatch.setattr(service, "get_core_client", lambda: fake)
    return fake


@pytest.fixture
def auth_headers(client):
    """Сид-пользователь user: вход и подписка на детскую одежду."""
    token = client.post("/api/v1/auth/login",
                        json={"username": "user", "password": "1234"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.put("/api/v1/subscriptions", json=[KIDS], headers=headers)
    assert resp.status_code == 200
    return headers


def _create(client, headers, **fields):
    return client.post("/api/v1/niches", json={**KIDS, **fields}, headers=headers)


def _niche_run(db_session, niche_id: UUID):
    """Готовый снепшот EWMA по нише - как после планового расчёта."""
    curve = pd.DataFrame({"parsed_at": [pd.Timestamp("2026-09-01 00:37"),
                                        pd.Timestamp("2026-09-01 12:37")],
                          "level_value": [0.0, 0.01]})
    result = SimpleNamespace(
        set_id=niche_id, category=KIDS["category"], level="niche", level_key=str(niche_id),
        algorithm="EWMA", n_cards=30, total_quota=30, deficit_share=0.0, scale_factor=1.0,
        sufficient=True, insufficient_reason=None, warning=None,
        events=pd.DataFrame(), curve=curve)
    save_run(db_session, result, marketplace=KIDS["marketplace"], geo=KIDS["geo"],
             grid_point_at=pd.Timestamp("2026-09-01 12:37"))
    db_session.commit()


def test_niches_require_auth(client):
    assert client.get("/api/v1/niches").status_code in (401, 403)
    assert client.get("/api/v1/niches/dashboard", params=KIDS).status_code in (401, 403)


def test_create_and_read(client, auth_headers, core_client):
    resp = _create(client, auth_headers, description="платье для девочки 7 лет",
                   reference_url=REF_URL)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["is_own"] is True
    assert body["core_status"] is None and body["search_query"] is None
    assert "message" not in body
    assert core_client.created == [UUID(body["id"])]

    listed = client.get("/api/v1/niches", params=KIDS, headers=auth_headers).json()
    assert body["id"] in {n["id"] for n in listed}
    one = client.get(f"/api/v1/niches/{body['id']}", headers=auth_headers)
    assert one.status_code == 200
    assert one.json()["reference_url"] == REF_URL


def test_create_with_reference_only(client, auth_headers, core_client):
    resp = _create(client, auth_headers, reference_url=REF_URL)
    assert resp.status_code == 201
    assert resp.json()["description"] is None


@pytest.mark.parametrize("fields, detail", [
    ({}, "ссылку на карточку-референс"),
    ({"description": "платье для девочки 7 лет"}, "ссылку на карточку-референс"),
    ({"reference_url": "   "}, "ссылку на карточку-референс"),
    ({"reference_url": "https://www.wildberries.ru/catalog/1/detail.aspx"}, "не на ozon"),
    ({"reference_url": "https://ozon.ru/t/AbCdEf1"}, "не на карточку товара"),
])
def test_create_validation(client, auth_headers, core_client, fields, detail):
    resp = _create(client, auth_headers, **fields)
    assert resp.status_code == 400
    assert detail in resp.json()["detail"]
    assert core_client.created == []


def test_create_for_unsubscribed_combo(client, auth_headers, core_client):
    resp = client.post("/api/v1/niches",
                       json={"category": "clothing", "marketplace": "ozon",
                             "geo": "Санкт-Петербург", "description": "платье",
                             "reference_url": REF_URL},
                       headers=auth_headers)
    assert resp.status_code == 400
    assert core_client.created == []


def test_limit_and_delete(client, auth_headers, core_client):
    ids = [_create(client, auth_headers, description=f"ниша {i}",
                   reference_url=REF_URL).json()["id"] for i in range(2)]
    assert _create(client, auth_headers, description="лишняя",
                   reference_url=REF_URL).status_code == 403
    assert client.get("/api/v1/niches/limits", headers=auth_headers).json()["used"] == 2

    resp = client.delete(f"/api/v1/niches/{ids[0]}", headers=auth_headers)
    assert resp.status_code == 200
    assert core_client.deleted == [UUID(ids[0])]
    listed = {n["id"] for n in client.get("/api/v1/niches", headers=auth_headers).json()}
    assert ids[0] not in listed and ids[1] in listed
    assert _create(client, auth_headers, description="вместо удалённой",
                   reference_url=REF_URL).status_code == 201


def test_get_unknown_and_malformed_id(client, auth_headers):
    assert client.get(f"/api/v1/niches/{uuid4()}", headers=auth_headers).status_code == 404
    assert client.get("/api/v1/niches/not-a-uuid", headers=auth_headers).status_code == 422


def test_dashboard_and_snapshot(client, auth_headers, core_client, db_session):
    niche_id = _create(client, auth_headers, description="платье",
                       reference_url=REF_URL).json()["id"]
    # подбор в ядре не завершён и расчётов нет - на дашборде ниши нет
    assert client.get("/api/v1/niches/dashboard", params=KIDS,
                      headers=auth_headers).json() == []

    # подбор завершён (active), расчёта ещё нет - ниша на дашборде, ready = False
    niche = db_session.get(Niche, UUID(niche_id))
    niche.core_status = "active"
    niche.search_query = "платье для девочки 7 лет"
    db_session.commit()
    expected = {"id": niche_id, "search_query": "платье для девочки 7 лет",
                "description": "платье", "reference_url": REF_URL, "is_own": True}
    dash = client.get("/api/v1/niches/dashboard", params=KIDS, headers=auth_headers)
    assert dash.status_code == 200
    assert dash.json() == [{**expected, "ready": False}]

    # готовый расчёт - ready = True
    _niche_run(db_session, UUID(niche_id))
    dash = client.get("/api/v1/niches/dashboard", params=KIDS, headers=auth_headers)
    assert dash.json() == [{**expected, "ready": True}]

    params = {**KIDS, "level": "niche", "level_key": niche_id, "algorithm": "EWMA"}
    snap = client.get("/api/v1/snapshot", params=params)
    assert snap.status_code == 200
    assert snap.json()["meta"]["level"] == "niche"
    assert snap.json()["meta"]["n_cards"] == 30

    chart = client.get("/api/v1/chart.png", params=params)
    assert chart.status_code == 200
    assert chart.headers["content-type"] == "image/png"

    assert client.get("/api/v1/levels", params={**KIDS, "level": "niche"}).status_code == 422


def test_analogs(client, auth_headers, core_client, monkeypatch):
    niche_id = _create(client, auth_headers, description="лонгслив для девочки",
                       reference_url=REF_URL).json()["id"]

    # 35 активных с близостью 0.900..0.934, выбывшая с самой высокой и активная без близости
    rows = [{"sku": 1000 + i, "name": f"Лонгслив {i}",
             "url": f"https://www.ozon.ru/product/{1000 + i}/",
             "status": "active", "similarity": 0.900 + i / 1000} for i in range(35)]
    rows.append({"sku": 2000, "name": "Выбывший", "url": None,
                 "status": "dropped", "similarity": 0.99})
    rows.append({"sku": 2001, "name": "Без близости", "url": None,
                 "status": "active", "similarity": None})
    cards = pd.DataFrame(rows).sample(frac=1, random_state=1)

    requested = []

    def get_niche_cards(nid):
        requested.append(nid)
        return cards

    monkeypatch.setattr(service, "_core_connector",
                        lambda: SimpleNamespace(get_niche_cards=get_niche_cards))

    resp = client.get(f"/api/v1/niches/{niche_id}/analogs", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert requested == [niche_id]
    assert len(body) == 30
    sims = [a["similarity"] for a in body]
    assert sims == sorted(sims, reverse=True)
    assert body[0]["sku"] == "1034" and body[0]["name"] == "Лонгслив 34"
    assert body[0]["url"] == "https://www.ozon.ru/product/1034/"
    assert abs(body[0]["similarity"] - 0.934) < 1e-9
    assert "unit_price" not in body[0]
    assert body[-1]["sku"] == "1005"
    assert {a["sku"] for a in body}.isdisjoint({"2000", "2001"})

    assert client.get(f"/api/v1/niches/{uuid4()}/analogs",
                      headers=auth_headers).status_code == 404