# ---------- Шлюз админки: только администратор, пересылка в ядро как есть ----------
import json
from uuid import uuid4

from app.api import admin as admin_api
from app.core_api.client import CoreUnavailable
from app.users.models import User
from app.users.security import create_access_token


class FakeResponse:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self.content = b"" if body is None else json.dumps(body).encode("utf-8")
        self.text = self.content.decode("utf-8")

    def json(self):
        return json.loads(self.content)


class FakeCore:
    """Ядро для шлюза: запоминает запросы, отвечает заданным ответом или ошибкой связи."""
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.response, self.error, self.calls = response, error, []

    def admin_request(self, method, path, params, content):
        self.calls.append((method, path, params, content))
        if self.error:
            raise self.error
        return self.response


def _headers(db_session, is_admin: bool) -> dict:
    user = User(username=f"adm-{uuid4().hex[:8]}", password_hash="x", is_admin=is_admin)
    db_session.add(user)
    db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.username)}"}


def test_without_token_rejected(client):
    assert client.get("/api/v1/admin/overview").status_code in (401, 403)


def test_non_admin_forbidden(client, db_session, monkeypatch):
    fake = FakeCore(FakeResponse(200, {}))
    monkeypatch.setattr(admin_api, "get_core_client", lambda: fake)
    r = client.get("/api/v1/admin/overview", headers=_headers(db_session, False))
    assert r.status_code == 403
    assert fake.calls == []                                     # в ядро не ушло


def test_admin_get_forwarded_with_params(client, db_session, monkeypatch):
    fake = FakeCore(FakeResponse(200, {"seasonal_switch": True}))
    monkeypatch.setattr(admin_api, "get_core_client", lambda: fake)
    params = {"category": "kids_clothing", "geo": "Санкт-Петербург", "marketplace": "ozon"}

    r = client.get("/api/v1/admin/overview", params=params, headers=_headers(db_session, True))

    assert r.status_code == 200 and r.json() == {"seasonal_switch": True}
    method, path, sent_params, content = fake.calls[0]
    assert (method, path, sent_params, content) == ("GET", "/api/v1/admin/overview", params, None)


def test_admin_patch_forwarded_with_body(client, db_session, monkeypatch):
    fake = FakeCore(FakeResponse(200, {"set": {"status": "draft"}}))
    monkeypatch.setattr(admin_api, "get_core_client", lambda: fake)

    r = client.patch("/api/v1/admin/drafts/d1/strata/s1", json={"query_share": 0.5},
                     headers=_headers(db_session, True))

    assert r.status_code == 200
    method, path, _, content = fake.calls[0]
    assert (method, path) == ("PATCH", "/api/v1/admin/drafts/d1/strata/s1")
    assert json.loads(content) == {"query_share": 0.5}


def test_core_answers_passed_through(client, db_session, monkeypatch):
    headers = _headers(db_session, True)
    monkeypatch.setattr(admin_api, "get_core_client",
                        lambda: FakeCore(FakeResponse(409, {"error": "уже есть черновик"})))
    r = client.post("/api/v1/admin/sets/a1/draft", headers=headers)
    assert r.status_code == 409 and r.json() == {"error": "уже есть черновик"}

    monkeypatch.setattr(admin_api, "get_core_client", lambda: FakeCore(FakeResponse(204)))
    assert client.delete("/api/v1/admin/drafts/d1", headers=headers).status_code == 204


def test_core_failures_mapped(client, db_session, monkeypatch):
    headers = _headers(db_session, True)
    monkeypatch.setattr(admin_api, "get_core_client",
                        lambda: FakeCore(error=CoreUnavailable("ConnectError")))
    assert client.get("/api/v1/admin/overview", headers=headers).status_code == 503

    monkeypatch.setattr(admin_api, "get_core_client",
                        lambda: FakeCore(FakeResponse(401, {"error": "Invalid or missing API key"})))
    r = client.get("/api/v1/admin/overview", headers=headers)
    assert r.status_code == 502 and r.json() == {"error": "core_rejected_service_key"}

    monkeypatch.setattr(admin_api, "get_core_client", lambda: FakeCore(FakeResponse(500, {"x": 1})))
    assert client.get("/api/v1/admin/overview", headers=headers).status_code == 502


def test_unsafe_path_not_forwarded(client, db_session, monkeypatch):
    fake = FakeCore(FakeResponse(200, {}))
    monkeypatch.setattr(admin_api, "get_core_client", lambda: fake)
    r = client.get("/api/v1/admin/drafts/a;b", headers=_headers(db_session, True))
    assert r.status_code == 404
    assert fake.calls == []


def test_me_reports_is_admin(client, db_session):
    assert client.get("/api/v1/auth/me", headers=_headers(db_session, True)).json()["is_admin"] is True
    assert client.get("/api/v1/auth/me", headers=_headers(db_session, False)).json()["is_admin"] is False