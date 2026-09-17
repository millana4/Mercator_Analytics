# ---------- Проверка идемпотентности сидирования тестового пользователя ----------
from sqlalchemy import select

from app.users.models import User
from app.users.seed import seed_test_user


def test_seed_creates_user_once(db_session, monkeypatch):
    # seed_test_user сам открывает SessionLocal() - подменяем на db_session,
    # чтобы создание пользователя тоже попало под откат теста
    monkeypatch.setattr("app.users.seed.SessionLocal", lambda: db_session)

    seed_test_user()
    users = db_session.execute(select(User)).scalars().all()
    assert len(users) == 1
    assert users[0].username == "user"


def test_seed_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr("app.users.seed.SessionLocal", lambda: db_session)

    seed_test_user()
    seed_test_user()  # повторный вызов не должен создать вторую запись

    users = db_session.execute(select(User)).scalars().all()
    assert len(users) == 1