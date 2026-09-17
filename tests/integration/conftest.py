# ---------- Поднимает тестовые Postgres-базы: analytics_test и core_test ----------
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.data_files import find_latest_pg_dump

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "backups"

TEST_PG_HOST = "127.0.0.1"
TEST_PG_PORT = 5499
TEST_PG_ADMIN_USER = "test"
TEST_PG_ADMIN_PASSWORD = "test"
ANALYTICS_TEST_DB = "analytics_test"
CORE_TEST_DB = "core_test"


@pytest.fixture(scope="session")
def test_databases():
    """Пересоздаёт обе тестовые базы с нуля. Требует поднятый
    docker-compose.test.yml (test-postgres на порту 5499)."""
    admin_dsn = (f"host={TEST_PG_HOST} port={TEST_PG_PORT} "
                f"user={TEST_PG_ADMIN_USER} password={TEST_PG_ADMIN_PASSWORD} dbname=postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for db in (ANALYTICS_TEST_DB, CORE_TEST_DB):
                cur.execute(f"DROP DATABASE IF EXISTS {db}")
                cur.execute(f"CREATE DATABASE {db}")
            # дамп ядра назначает владельцем card/set/... роль taskmanager -
            # роль должна существовать, иначе ALTER TABLE ... OWNER TO упадёт
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'taskmanager'")
            if cur.fetchone() is None:
                cur.execute("CREATE ROLE taskmanager")
    yield


@pytest.fixture(scope="session")
def core_test_db(test_databases):
    """Восстанавливает дамп ядра в core_test - нужен только тестам RealConnector."""
    try:
        dump_path = find_latest_pg_dump(DATA_DIR)
    except FileNotFoundError as e:
        pytest.skip(str(e))
    subprocess.run(
        ["psql", "-h", TEST_PG_HOST, "-p", str(TEST_PG_PORT),
         "-U", TEST_PG_ADMIN_USER, "-d", CORE_TEST_DB, "-f", str(dump_path)],
        check=True, env={**os.environ, "PGPASSWORD": TEST_PG_ADMIN_PASSWORD},
        capture_output=True, text=True,
    )
    yield


@pytest.fixture(scope="session")
def analytics_test_db(test_databases):
    """Накатывает миграции нашей схемы в analytics_test - тем же alembic,
    которым накатывали руками все предыдущие шаги."""
    env = {**os.environ,
           "ANALYTICS_PG_HOST": TEST_PG_HOST, "ANALYTICS_PG_PORT": str(TEST_PG_PORT),
           "ANALYTICS_PG_DB": ANALYTICS_TEST_DB,
           "ANALYTICS_PG_USER": TEST_PG_ADMIN_USER, "ANALYTICS_PG_PASSWORD": TEST_PG_ADMIN_PASSWORD}
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, cwd=REPO_ROOT)
    yield


@pytest.fixture(scope="session")
def app_with_client(analytics_test_db):
    """Импортирует приложение один раз за сессию тестов - lifespan (сид
    testUser) отрабатывает здесь же, один раз."""
    from app.main import app
    with TestClient(app) as c:
        yield app, c


@pytest.fixture(scope="session")
def _engine(app_with_client):
    from app.db.session import engine
    return engine


@pytest.fixture
def db_session(_engine):
    """Своя транзакция на каждый тест. commit() внутри вызываемого кода
    фиксирует только savepoint - в конце теста всё откатывается целиком,
    следующий тест видит чистую базу."""
    connection = _engine.connect()
    trans = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    yield session
    session.close()
    trans.rollback()
    connection.close()


@pytest.fixture
def client(app_with_client, db_session):
    """TestClient, у которого зависимость get_session подменена на
    db_session - запросы через API откатятся вместе с остальным тестом."""
    app, raw_client = app_with_client
    from app.db.session import get_session

    app.dependency_overrides[get_session] = lambda: db_session
    yield raw_client
    app.dependency_overrides.pop(get_session, None)