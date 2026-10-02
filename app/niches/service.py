# ---------- Товарные ниши: видимость, лимит, создание, удаление ----------
import logging
from datetime import datetime
from functools import lru_cache
from uuid import UUID

import pandas as pd
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.config import policies
from app.connectors.factory import get_connector
from app.core_api.client import CoreRejected, CoreUnavailable, get_core_client
from app.niches.models import Niche, NicheStatus, UserNiche
from app.niches.validation import ReferenceUrlError, validate_reference_url
from app.users.models import User, UserSubscription
from app.db.session import SessionLocal
from app.db import repository as repo

log = logging.getLogger(__name__)


class NicheError(Exception):
    """Ошибка для пользователя: HTTP-код и текст."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------- 1. Лимит ----------
def free_limit(user: User) -> int:
    return policies.FREE_NICHES_INTERNAL if user.is_internal else policies.FREE_NICHES_EXTERNAL


def active_owned_count(session: Session, user_id: UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(Niche)
        .where(Niche.owner_id == user_id, Niche.status != NicheStatus.DELETED)
    )


def limits(session: Session, user: User) -> dict:
    return {"used": active_owned_count(session, user.id),
            "limit": free_limit(user),
            "is_internal": user.is_internal}


# ---------- 2. Видимость ----------
def _visible_query(user: User):
    """Неудалённые ниши, доступные пользователю по режиму видимости."""
    q = select(Niche).where(Niche.status != NicheStatus.DELETED)
    if policies.NICHE_VISIBILITY == "own":
        q = q.join(UserNiche, UserNiche.niche_id == Niche.id).where(UserNiche.user_id == user.id)
    else:
        subs = (select(UserSubscription.category, UserSubscription.marketplace, UserSubscription.geo)
                .where(UserSubscription.user_id == user.id))
        q = q.where(tuple_(Niche.category, Niche.marketplace, Niche.geo).in_(subs))
    return q


def list_available(session: Session, user: User, category: str | None = None,
                   marketplace: str | None = None, geo: str | None = None) -> list[Niche]:
    q = _visible_query(user)
    if category:
        q = q.where(Niche.category == category)
    if marketplace:
        q = q.where(Niche.marketplace == marketplace)
    if geo:
        q = q.where(Niche.geo == geo)
    return list(session.scalars(q.order_by(Niche.created_at.desc())))


def get_visible(session: Session, user: User, niche_id: UUID) -> Niche:
    niche = session.scalar(_visible_query(user).where(Niche.id == niche_id))
    if niche is None:
        raise NicheError(404, "Ниша не найдена")
    return niche


# ---------- 3. Создание ----------
def create(session: Session, user: User, category: str, marketplace: str, geo: str,
           description: str | None, reference_url: str | None) -> Niche:
    """Проверить и сохранить нишу, отправить в ядро. Если ядро недоступно,
    ниша остаётся pending и досылается планировщиком."""
    description = (description or "").strip() or None
    reference_url = (reference_url or "").strip() or None
    if description is None and reference_url is None:
        raise NicheError(400, "Заполните описание или ссылку на референс")

    subscribed = session.scalar(
        select(UserSubscription.id).where(
            UserSubscription.user_id == user.id,
            UserSubscription.category == category,
            UserSubscription.marketplace == marketplace,
            UserSubscription.geo == geo))
    if subscribed is None:
        raise NicheError(400, "Связка категория / маркетплейс / гео недоступна пользователю")

    if reference_url:
        try:
            validate_reference_url(marketplace, reference_url)
        except ReferenceUrlError as e:
            raise NicheError(400, str(e))

    # Лимит — под блокировкой строки пользователя: два одновременных запроса
    # не обойдут его. Блокировка снимается на commit.
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    used = active_owned_count(session, user.id)
    limit = free_limit(user)
    if used >= limit:
        session.rollback()
        raise NicheError(403, f"Лимит бесплатных ниш исчерпан: {used} из {limit}. "
                              f"Чтобы добавить новую, удалите одну из своих ниш.")

    niche = Niche(owner_id=user.id, category=category, marketplace=marketplace, geo=geo,
                  description=description, reference_url=reference_url,
                  status=NicheStatus.PENDING)
    session.add(niche)
    session.flush()
    session.add(UserNiche(user_id=user.id, niche_id=niche.id))
    session.commit()

    try:
        get_core_client().create_niche(niche)
    except CoreRejected as e:
        # Ядро отклонило — нишу не оставляем (user_niche удалится каскадом)
        session.delete(niche)
        session.commit()
        raise NicheError(400, f"Задание отклонено: {e}")
    except CoreUnavailable as e:
        niche.submit_error = str(e)[:500]
        session.commit()
        log.warning("Ниша %s сохранена, ядро недоступно — будет дослана: %s", niche.id, e)
        return niche

    niche.status = NicheStatus.SUBMITTED
    niche.submit_error = None
    session.commit()
    log.info("Ниша %s создана пользователем %s и отправлена в ядро", niche.id, user.username)
    return niche


# ---------- 4. Удаление ----------
def delete(session: Session, user: User, niche_id: UUID) -> None:
    """Удалить свою нишу — у всех. Удаление в ядре досылается, если ядро недоступно."""
    niche = session.get(Niche, niche_id)
    if niche is None or niche.status == NicheStatus.DELETED:
        raise NicheError(404, "Ниша не найдена")
    if niche.owner_id != user.id:
        raise NicheError(403, "Удалить можно только свою нишу")

    niche.status = NicheStatus.DELETED
    niche.deleted_at = datetime.now()
    niche.submit_error = None
    session.commit()

    # Отправляем и для pending: ядро могло принять задание, а ответ потеряться.
    # Если ниши в ядре нет, оно вернёт 404 — это тоже успех.
    try:
        get_core_client().delete_niche(niche.id)
    except CoreUnavailable as e:
        niche.submit_error = f"Удаление не отправлено в ядро: {e}"[:500]
        session.commit()
        log.warning("Ниша %s удалена, ядро недоступно — удаление будет дослано: %s", niche.id, e)
        return
    log.info("Ниша %s удалена пользователем %s", niche.id, user.username)


# ---------- 5. Досылка в ядро (планировщик) ----------
def resend_to_core() -> None:
    """Досылка: pending — задания, deleted с submit_error — удаления.
    После досылки - обновление состояния обработки из ядра (раздел 6)."""
    client = get_core_client()
    with SessionLocal() as session:
        pending = session.scalars(
            select(Niche).where(Niche.status == NicheStatus.PENDING)).all()
        for niche in pending:
            try:
                client.create_niche(niche)
            except CoreRejected as e:
                log.warning("Ниша %s отклонена ядром при досылке и удалена: %s", niche.id, e)
                session.delete(niche)
                session.commit()
                continue
            except CoreUnavailable as e:
                niche.submit_error = str(e)[:500]
                session.commit()
                continue
            niche.status = NicheStatus.SUBMITTED
            niche.submit_error = None
            session.commit()
            log.info("Ниша %s дослана в ядро", niche.id)

        unsent_deletes = session.scalars(
            select(Niche).where(Niche.status == NicheStatus.DELETED,
                                Niche.submit_error.is_not(None))).all()
        for niche in unsent_deletes:
            try:
                client.delete_niche(niche.id)
            except CoreUnavailable as e:
                niche.submit_error = f"Удаление не отправлено в ядро: {e}"[:500]
                session.commit()
                continue
            niche.submit_error = None
            session.commit()
            log.info("Удаление ниши %s дослано в ядро", niche.id)

    # Сбой синхронизации (ядро недоступно) досылку не отменяет
    try:
        sync_core_state()
    except Exception as e:
        log.warning("Состояние ниш из ядра не обновлено: %s", e)


# ---------- 6. Состояние обработки в ядре (планировщик) ----------
@lru_cache(maxsize=1)
def _core_connector():
    """Один коннектор на процесс: задача идёт раз в минуту,
    новое подключение к базе ядра на каждый запуск не нужно."""
    return get_connector()


def _nullable(value) -> str | None:
    return None if pd.isna(value) else str(value)


def sync_core_state() -> int:
    """Копирует из ядра статус обработки, поисковый запрос и ошибку в ниши,
    которые ядро приняло (submitted). Возвращает число изменённых ниш."""
    core = _core_connector().get_niches()
    by_id = {row["niche_id"]: row for _, row in core.iterrows()}

    changed = 0
    with SessionLocal() as session:
        niches = session.scalars(
            select(Niche).where(Niche.status == NicheStatus.SUBMITTED)).all()
        for niche in niches:
            row = by_id.get(str(niche.id))
            if row is None:
                continue
            state = (_nullable(row["status"]), _nullable(row["search_query"]),
                     _nullable(row["error"]))
            if state == (niche.core_status, niche.search_query, niche.core_error):
                continue
            niche.core_status, niche.search_query, niche.core_error = state
            changed += 1
        session.commit()

    if changed:
        log.info("Состояние ниш обновлено из ядра: %d", changed)
    return changed


# ---------- 7. Ниши для дашборда ----------
def dashboard(session: Session, user: User, category: str, marketplace: str,
              geo: str) -> list[Niche]:
    """Видимые пользователю ниши связки, у которых в последнем снепшоте хотя бы
    один алгоритм дал результат. Пока данных недостаточно, ниша не показывается."""
    niches = list_available(session, user, category, marketplace, geo)
    rows = repo.list_levels(session, category=category, geo=geo,
                            marketplace=marketplace, level="niche")
    ready = {r["level_key"] for r in rows if r["sufficient"]}
    return [n for n in niches if str(n.id) in ready]


# ---------- 8. Аналоги ниши ----------
def _sku(value) -> str:
    return value if isinstance(value, str) else str(int(value))


def analogs(session: Session, user: User, niche_id: UUID) -> list[dict]:
    """Самые близкие к референсу активные карточки ниши, по убыванию близости,
    не больше NICHE_ANALOGS_LIMIT. Состав и близость - из ядра; карточка без
    близости в список не попадает."""
    niche = get_visible(session, user, niche_id)
    cards = _core_connector().get_niche_cards(str(niche.id))
    active = cards[(cards["status"] == "active") & cards["similarity"].notna()]
    top = (active.sort_values("similarity", ascending=False, kind="stable")
           .head(policies.NICHE_ANALOGS_LIMIT))
    return [{"sku": _sku(r["sku"]), "name": _nullable(r["name"]),
             "url": _nullable(r["url"]), "similarity": float(r["similarity"])}
            for _, r in top.iterrows()]