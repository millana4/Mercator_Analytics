# ---------- GET/PUT /subscriptions ----------
import pytest


@pytest.fixture
def auth_headers(client):
    token = client.post("/api/v1/auth/login",
                        json={"username": "user", "password": "1234"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_empty_initially(client, auth_headers):
    resp = client.get("/api/v1/subscriptions", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_put_replaces_and_dedups(client, auth_headers):
    payload = [
        {"category": "kids_clothing", "marketplace": "ozon", "geo": "spb"},
        {"category": "clothing", "marketplace": "ozon", "geo": "spb"},
        {"category": "clothing", "marketplace": "ozon", "geo": "spb"},  # дубль
    ]
    resp = client.put("/api/v1/subscriptions", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    combos = {(s["category"], s["marketplace"], s["geo"]) for s in body}
    assert combos == {("kids_clothing", "ozon", "spb"), ("clothing", "ozon", "spb")}


def test_get_reflects_last_put(client, auth_headers):
    client.put("/api/v1/subscriptions",
              json=[{"category": "clothing", "marketplace": "ozon", "geo": "spb"}],
              headers=auth_headers)
    resp = client.get("/api/v1/subscriptions", headers=auth_headers)
    assert len(resp.json()) == 1


def test_second_put_fully_replaces_first(client, auth_headers):
    client.put("/api/v1/subscriptions",
              json=[{"category": "clothing", "marketplace": "ozon", "geo": "spb"}],
              headers=auth_headers)
    resp = client.put("/api/v1/subscriptions",
                      json=[{"category": "kids_clothing", "marketplace": "ozon", "geo": "spb"}],
                      headers=auth_headers)
    body = resp.json()
    assert len(body) == 1
    assert body[0]["category"] == "kids_clothing"  # старой clothing уже нет


def test_subscriptions_require_auth(client):
    resp = client.get("/api/v1/subscriptions")
    assert resp.status_code in (401, 403)