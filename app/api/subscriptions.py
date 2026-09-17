# ---------- Подписки пользователя: сохранённые сеты категория+маркетплейс+гео ----------
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import get_session
from app.users.models import User, UserSubscription

router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])


class SubscriptionIn(BaseModel):
    category: str
    marketplace: str
    geo: str


class SubscriptionOut(SubscriptionIn):
    id: str


@router.get("")
def list_subscriptions(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[SubscriptionOut]:
    rows = session.execute(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    ).scalars().all()
    return [SubscriptionOut(id=str(r.id), category=r.category,
                            marketplace=r.marketplace, geo=r.geo) for r in rows]


@router.put("")
def replace_subscriptions(
    body: list[SubscriptionIn],
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[SubscriptionOut]:
    """Заменяет весь список подписок пользователя сразу - ровно то, что
    делает кнопка "Сохранить настройки" на фронте. Дедупликация - через
    множество, а не через набор в БД: PUT всегда пересоздаёт список с нуля."""
    unique = {(s.category, s.marketplace, s.geo) for s in body}

    session.execute(delete(UserSubscription).where(UserSubscription.user_id == user.id))
    new_rows = [UserSubscription(user_id=user.id, category=c, marketplace=m, geo=g)
                for c, m, g in unique]
    session.add_all(new_rows)
    session.commit()

    return [SubscriptionOut(id=str(r.id), category=r.category,
                            marketplace=r.marketplace, geo=r.geo) for r in new_rows]