# ---------- Товарные ниши: задания пользователей ----------
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NicheStatus:
    """Статус ниши на стороне аналитики. Обработка в ядре - отдельно, core_status."""
    PENDING = "pending"        # сохранена, в ядро ещё не отправлена — досылается
    SUBMITTED = "submitted"    # ядро приняло
    DELETED = "deleted"        # удалена


class Niche(Base):
    """Задание ниши. id уходит в ядро как external_id."""
    __tablename__ = "niche"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(64))
    marketplace: Mapped[str] = mapped_column(String(64))
    geo: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    reference_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default=NicheStatus.PENDING,
                                        default=NicheStatus.PENDING)
    submit_error: Mapped[str | None] = mapped_column(Text)   # причина, по которой не отправлена
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Состояние обработки в ядре. Копируется задачей niche_resend (раз в 60 с),
    # только у принятых ядром ниш. До первой синхронизации пусто.
    core_status: Mapped[str | None] = mapped_column(String(32))   # new ... active, failed, deleted
    search_query: Mapped[str | None] = mapped_column(Text)        # запрос нейросети - подпись ниши
    core_error: Mapped[str | None] = mapped_column(Text)          # причина failed в ядре

    __table_args__ = (
        CheckConstraint("description IS NOT NULL OR reference_url IS NOT NULL",
                        name="niche_input_check"),
        CheckConstraint("status IN ('pending', 'submitted', 'deleted')",
                        name="niche_status_check"),
    )


class UserNiche(Base):
    """Связка пользователь — ниша (владелец записывается при создании)."""
    __tablename__ = "user_niche"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"),
                                          primary_key=True)
    niche_id: Mapped[UUID] = mapped_column(ForeignKey("niche.id", ondelete="CASCADE"),
                                           primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())