# ---------- Сервис ниш на тестовой базе: лимит, видимость, синхронизация, дашборд ----------
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from app.config import policies
from app.connectors import schema
from app.db.repository import save_run
from app.niches import service
from app.niches.models import Niche, NicheStatus, UserNiche
from app.niches.service import NicheError
from app.users.models import User, UserSubscription

KIDS = ("kids_clothing", "ozon", "Санкт-Петербург")
ADULT = ("clothing", "ozon", "Санкт-Петербург")
REF_URL = "https://www.ozon.ru/product/platye-dlya-devochki-123456789/"


# ---------- 1. Данные теста ----------
def _user(session, *, is_internal=True, subscriptions=(KIDS,)) -> User:
    user = User(username=f"test-{uuid4().hex[:8]}", password_hash="x", is_internal=is_internal)
    session.add(user)
    session.flush()
    for category, marketplace, geo in subscriptions:
        session.add(UserSubscription(user_id=user.id, category=category,
                                     marketplace=marketplace, geo=geo))
    session.commit()
    return user


def _niche(session, owner, *, combo=KIDS, status=NicheStatus.SUBMITTED) -> Niche:
    """Ниша напрямую в базе, минуя create(); владелец записан и в user_niche."""
    category, marketplace, geo = combo
    niche = Niche(owner_id=owner.id, category=category, marketplace=marketplace, geo=geo,
                  description="тестовая ниша", reference_url=REF_URL, status=status)
    session.add(niche)
    session.flush()
    session.add(UserNiche(user_id=owner.id, niche_id=niche.id))
    session.commit()
    return niche


def _core_row(niche, status, search_query=None, error=None) -> dict:
    return {"niche_id": str(niche.id), "marketplace": niche.marketplace,
            "category": niche.category, "geo": niche.geo, "status": status,
            "search_query": search_query, "error": error,
            "target_count": 30, "plan_count": 40}


class FakeCoreClient:
    """Ядро, которое принимает создание и удаление ниш."""

    def __init__(self):
        self.created, self.deleted = [], []

    def create_niche(self, niche):
        self.created.append(niche.id)

    def delete_niche(self, niche_id):
        self.deleted.append(niche_id)


class FakeCoreConnector:
    """Коннектор с заранее заданными нишами ядра или с ошибкой доступа."""

    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error

    def get_niches(self) -> pd.DataFrame:
        if self.error is not None:
            raise self.error
        return schema.conform(pd.DataFrame(self.rows, columns=list(schema.NICHES)),
                              schema.NICHES)


@pytest.fixture
def core_client(monkeypatch):
    fake = FakeCoreClient()
    monkeypatch.setattr(service, "get_core_client", lambda: fake)
    return fake


@pytest.fixture
def same_session(monkeypatch, db_session):
    """Фоновые функции открывают SessionLocal(). В тесте это та же db_session,
    иначе они не увидят данные теста и писали бы в базу мимо отката."""
    monkeypatch.setattr(service, "SessionLocal", lambda: nullcontext(db_session))


def _set_core(monkeypatch, connector):
    monkeypatch.setattr(service, "_core_connector", lambda: connector)


# ---------- 2. Создание, лимит и удаление ----------
def test_create_requires_reference_url(db_session, core_client):
    user = _user(db_session)
    with pytest.raises(NicheError) as e:
        service.create(db_session, user, *KIDS, "платье для девочки", None)
    assert e.value.status_code == 400
    assert "ссылку на карточку-референс" in e.value.message
    assert core_client.created == []


def test_limit_internal_user(db_session, core_client, monkeypatch):
    monkeypatch.setattr(policies, "FREE_NICHES_INTERNAL", 2)
    user = _user(db_session, is_internal=True)
    for _ in range(2):
        niche = service.create(db_session, user, *KIDS, "платье", REF_URL)
        assert niche.status == NicheStatus.SUBMITTED
    with pytest.raises(NicheError) as e:
        service.create(db_session, user, *KIDS, "ещё одно платье", REF_URL)
    assert e.value.status_code == 403
    assert "2 из 2" in e.value.message
    assert service.limits(db_session, user) == {"used": 2, "limit": 2, "is_internal": True}
    assert len(core_client.created) == 2


def test_limit_external_user(db_session, core_client, monkeypatch):
    monkeypatch.setattr(policies, "FREE_NICHES_EXTERNAL", 1)
    user = _user(db_session, is_internal=False)
    service.create(db_session, user, *KIDS, "платье", REF_URL)
    with pytest.raises(NicheError) as e:
        service.create(db_session, user, *KIDS, "второе платье", REF_URL)
    assert e.value.status_code == 403
    assert service.limits(db_session, user)["limit"] == 1


def test_delete_frees_slot(db_session, core_client, monkeypatch):
    monkeypatch.setattr(policies, "FREE_NICHES_EXTERNAL", 1)
    user = _user(db_session, is_internal=False)
    niche = service.create(db_session, user, *KIDS, "платье", REF_URL)
    service.delete(db_session, user, niche.id)
    assert core_client.deleted == [niche.id]
    service.create(db_session, user, *KIDS, "новое платье", REF_URL)   # место освободилось
    assert service.limits(db_session, user)["used"] == 1


def test_cannot_delete_foreign_niche(db_session, core_client):
    owner, other = _user(db_session), _user(db_session)
    niche = _niche(db_session, owner)
    with pytest.raises(NicheError) as e:
        service.delete(db_session, other, niche.id)
    assert e.value.status_code == 403
    db_session.refresh(niche)
    assert niche.status == NicheStatus.SUBMITTED
    assert core_client.deleted == []


def test_create_requires_subscription(db_session, core_client):
    user = _user(db_session, subscriptions=(KIDS,))
    with pytest.raises(NicheError) as e:
        service.create(db_session, user, *ADULT, "платье", REF_URL)
    assert e.value.status_code == 400
    assert core_client.created == []


# ---------- 3. Видимость ----------
def _visible_ids(session, user) -> set:
    return {n.id for n in service.list_available(session, user)}


def test_visibility_all_by_subscription(db_session, monkeypatch):
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", "all")
    owner = _user(db_session)
    subscriber = _user(db_session, subscriptions=(KIDS,))
    stranger = _user(db_session, subscriptions=(ADULT,))
    niche = _niche(db_session, owner)
    assert niche.id in _visible_ids(db_session, owner)
    assert niche.id in _visible_ids(db_session, subscriber)
    assert niche.id not in _visible_ids(db_session, stranger)
    with pytest.raises(NicheError) as e:
        service.get_visible(db_session, stranger, niche.id)
    assert e.value.status_code == 404


def test_visibility_own_only(db_session, monkeypatch):
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", "own")
    owner, subscriber = _user(db_session), _user(db_session)
    niche = _niche(db_session, owner)
    assert niche.id in _visible_ids(db_session, owner)
    assert niche.id not in _visible_ids(db_session, subscriber)


@pytest.mark.parametrize("mode", ["all", "own"])
def test_deleted_niche_invisible(db_session, monkeypatch, mode):
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", mode)
    owner = _user(db_session)
    niche = _niche(db_session, owner, status=NicheStatus.DELETED)
    assert niche.id not in _visible_ids(db_session, owner)


# ---------- 4. Синхронизация с ядром ----------
def test_sync_updates_only_submitted(db_session, same_session, monkeypatch):
    owner = _user(db_session)
    submitted = _niche(db_session, owner)
    pending = _niche(db_session, owner, status=NicheStatus.PENDING)
    deleted = _niche(db_session, owner, status=NicheStatus.DELETED)
    unknown = _niche(db_session, owner)          # принята, но в выборке ядра её нет
    _set_core(monkeypatch, FakeCoreConnector([
        _core_row(submitted, "selecting", "платье для девочки 7 лет"),
        _core_row(pending, "active", "не должно попасть"),
        _core_row(deleted, "deleted", "не должно попасть"),
    ]))
    assert service.sync_core_state() == 1
    db_session.expire_all()
    assert (submitted.core_status, submitted.search_query, submitted.core_error) == \
        ("selecting", "платье для девочки 7 лет", None)
    for n in (pending, deleted, unknown):
        assert (n.core_status, n.search_query, n.core_error) == (None, None, None)


def test_sync_failed_state_and_no_repeat_writes(db_session, same_session, monkeypatch):
    owner = _user(db_session)
    niche = _niche(db_session, owner)
    _set_core(monkeypatch, FakeCoreConnector([
        _core_row(niche, "failed", None, "нейросеть не ответила за 3 попытки")]))
    assert service.sync_core_state() == 1
    assert service.sync_core_state() == 0        # состояние не изменилось - записи нет
    db_session.expire_all()
    assert niche.core_status == "failed"
    assert niche.core_error == "нейросеть не ответила за 3 попытки"
    assert niche.search_query is None


def test_resend_submits_pending_then_syncs(db_session, same_session, core_client, monkeypatch):
    owner = _user(db_session)
    niche = _niche(db_session, owner, status=NicheStatus.PENDING)
    _set_core(monkeypatch, FakeCoreConnector([_core_row(niche, "new")]))
    service.resend_to_core()
    db_session.expire_all()
    assert core_client.created == [niche.id]
    assert niche.status == NicheStatus.SUBMITTED
    assert niche.core_status == "new"


def test_resend_survives_core_db_failure(db_session, same_session, core_client, monkeypatch):
    owner = _user(db_session)
    niche = _niche(db_session, owner, status=NicheStatus.PENDING)
    _set_core(monkeypatch, FakeCoreConnector(error=RuntimeError("база ядра недоступна")))
    service.resend_to_core()                     # не падает
    db_session.expire_all()
    assert niche.status == NicheStatus.SUBMITTED
    assert niche.core_status is None


# ---------- 5. Дашборд ----------
def _run(session, niche, *, algorithm="EWMA", sufficient=True, grid_point="2026-09-01 00:37"):
    result = SimpleNamespace(
        set_id=niche.id, category=niche.category, level="niche", level_key=str(niche.id),
        algorithm=algorithm, n_cards=30 if sufficient else 0, total_quota=30,
        deficit_share=0.0, scale_factor=1.0, sufficient=sufficient,
        insufficient_reason=None if sufficient else "мало карточек", warning=None,
        events=pd.DataFrame(), curve=pd.DataFrame())
    save_run(session, result, marketplace=niche.marketplace, geo=niche.geo,
             grid_point_at=pd.Timestamp(grid_point))
    session.commit()


def _dashboard_ids(session, user) -> set:
    return {n.id for n in service.dashboard(session, user, *KIDS)}


def test_dashboard_only_ready_niches(db_session, monkeypatch):
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", "all")
    user = _user(db_session)
    ready = _niche(db_session, user)
    partly = _niche(db_session, user)            # готов один алгоритм - этого достаточно
    not_ready = _niche(db_session, user)
    no_runs = _niche(db_session, user)
    deleted = _niche(db_session, user, status=NicheStatus.DELETED)
    for algorithm in ("EWMA", "MACD"):
        _run(db_session, ready, algorithm=algorithm)
        _run(db_session, not_ready, algorithm=algorithm, sufficient=False)
        _run(db_session, deleted, algorithm=algorithm)
    _run(db_session, partly, algorithm="EWMA")
    _run(db_session, partly, algorithm="MACD", sufficient=False)
    ids = _dashboard_ids(db_session, user)
    assert ready.id in ids and partly.id in ids
    assert not ({not_ready.id, no_runs.id, deleted.id} & ids)


def test_dashboard_ignores_old_cusum_runs(db_session, monkeypatch):
    # старый успешный расчёт снятого алгоритма не делает нишу готовой
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", "all")
    user = _user(db_session)
    niche = _niche(db_session, user)
    _run(db_session, niche, algorithm="CUSUM", sufficient=True)
    _run(db_session, niche, algorithm="EWMA", sufficient=False)
    _run(db_session, niche, algorithm="MACD", sufficient=False)
    assert niche.id not in _dashboard_ids(db_session, user)


def test_dashboard_uses_latest_snapshot(db_session, monkeypatch):
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", "all")
    user = _user(db_session)
    niche = _niche(db_session, user)
    _run(db_session, niche, sufficient=True, grid_point="2026-09-01 00:37")
    _run(db_session, niche, sufficient=False, grid_point="2026-09-01 12:37")
    assert niche.id not in _dashboard_ids(db_session, user)


def test_dashboard_respects_visibility(db_session, monkeypatch):
    monkeypatch.setattr(policies, "NICHE_VISIBILITY", "own")
    owner, other = _user(db_session), _user(db_session)
    niche = _niche(db_session, owner)
    _run(db_session, niche)
    assert niche.id in _dashboard_ids(db_session, owner)
    assert niche.id not in _dashboard_ids(db_session, other)