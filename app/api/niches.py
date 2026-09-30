# ---------- Товарные ниши: доступные, создание, удаление, подробности ----------
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import get_session
from app.niches import service
from app.niches.models import Niche
from app.niches.service import NicheError
from app.users.models import User

router = APIRouter(prefix="/api/v1/niches", tags=["niches"])


class NicheIn(BaseModel):
    category: str
    marketplace: str
    geo: str
    description: str | None = None
    reference_url: str | None = None


class NicheOut(BaseModel):
    id: str
    category: str
    marketplace: str
    geo: str
    description: str | None
    reference_url: str | None
    status: str
    is_own: bool
    created_at: datetime
    # состояние обработки в ядре; пусто до первой синхронизации
    core_status: str | None
    search_query: str | None
    core_error: str | None


class NicheCreatedOut(NicheOut):
    message: str


class LimitsOut(BaseModel):
    used: int
    limit: int
    is_internal: bool


class DashboardNicheOut(BaseModel):
    """Ниша для блока «Товарная ниша»: подпись - search_query,
    «Подробнее о товарной нише» - description и reference_url."""
    id: str
    search_query: str | None
    description: str | None
    reference_url: str | None
    is_own: bool


def _out(niche: Niche, user: User) -> NicheOut:
    return NicheOut(id=str(niche.id), category=niche.category, marketplace=niche.marketplace,
                    geo=niche.geo, description=niche.description,
                    reference_url=niche.reference_url, status=niche.status,
                    is_own=niche.owner_id == user.id, created_at=niche.created_at,
                    core_status=niche.core_status, search_query=niche.search_query,
                    core_error=niche.core_error)


def _raise(e: NicheError):
    raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("")
def list_niches(category: str | None = None, marketplace: str | None = None,
                geo: str | None = None,
                user: User = Depends(get_current_user),
                session: Session = Depends(get_session)) -> list[NicheOut]:
    return [_out(n, user) for n in service.list_available(session, user, category, marketplace, geo)]


# /limits и /dashboard объявлены до /{niche_id}, иначе их имена разбирались бы как id
@router.get("/limits")
def get_limits(user: User = Depends(get_current_user),
               session: Session = Depends(get_session)) -> LimitsOut:
    return LimitsOut(**service.limits(session, user))


@router.get("/dashboard")
def dashboard_niches(category: str, marketplace: str, geo: str,
                     user: User = Depends(get_current_user),
                     session: Session = Depends(get_session)) -> list[DashboardNicheOut]:
    """Ниши с готовым результатом. Данные ниши - /api/v1/snapshot?level=niche&level_key=<id>."""
    return [DashboardNicheOut(id=str(n.id), search_query=n.search_query,
                              description=n.description, reference_url=n.reference_url,
                              is_own=n.owner_id == user.id)
            for n in service.dashboard(session, user, category, marketplace, geo)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_niche(body: NicheIn,
                 user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)) -> NicheCreatedOut:
    try:
        niche, message = service.create(session, user, body.category, body.marketplace,
                                        body.geo, body.description, body.reference_url)
    except NicheError as e:
        _raise(e)
    return NicheCreatedOut(**_out(niche, user).model_dump(), message=message)


@router.get("/{niche_id}")
def get_niche(niche_id: UUID,
              user: User = Depends(get_current_user),
              session: Session = Depends(get_session)) -> NicheOut:
    try:
        return _out(service.get_visible(session, user, niche_id), user)
    except NicheError as e:
        _raise(e)


@router.delete("/{niche_id}")
def delete_niche(niche_id: UUID,
                 user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)) -> dict:
    try:
        service.delete(session, user, niche_id)
    except NicheError as e:
        _raise(e)
    return {"id": str(niche_id), "status": "deleted"}