# ---------- POST /auth/login, GET /auth/me ----------
def test_login_success(client):
    resp = client.post("/api/v1/auth/login",
                       json={"username": "user", "password": "1234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password(client):
    resp = client.post("/api/v1/auth/login",
                       json={"username": "user", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_username(client):
    resp = client.post("/api/v1/auth/login",
                       json={"username": "no-such-user", "password": "1234"})
    assert resp.status_code == 401


def test_me_with_valid_token(client):
    token = client.post("/api/v1/auth/login",
                        json={"username": "user", "password": "1234"}).json()["access_token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "user"


def test_me_without_token(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)  # HTTPBearer по умолчанию отдаёт 403 без заголовка


def test_me_with_garbage_token(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
    assert resp.status_code == 401