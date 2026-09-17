# ---------- Идемпотентное создание тестового пользователя при старте ----------
import logging

import bcrypt
from sqlalchemy import select

from app.db.session import SessionLocal
from app.users.models import User

logger = logging.getLogger(__name__)

TEST_USERNAME = "user"
TEST_PASSWORD = "1234"


def seed_test_user() -> None:
    """Создаёт тестового пользователя, если его ещё нет. Безопасно вызывать
    при каждом старте приложения - при повторном запуске ничего не делает."""
    with SessionLocal() as session:
        existing = session.execute(
            select(User).where(User.username == TEST_USERNAME)
        ).scalar_one_or_none()
        if existing:
            return

        password_hash = bcrypt.hashpw(
            TEST_PASSWORD.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        session.add(User(username=TEST_USERNAME, password_hash=password_hash))
        session.commit()
        logger.info("Создан тестовый пользователь %s", TEST_USERNAME)