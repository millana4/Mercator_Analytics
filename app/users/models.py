# ---------- Таблица пользователей ----------
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, func, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    # unique=True + index=True — уникальный индекс, он и есть защита от дублей логина
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Внутренний клиент: больше бесплатных ниш (policies.FREE_NICHES_INTERNAL).
    # По умолчанию true на время тестирования; позже — false и админ-панель.
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserSubscription(Base):
    __tablename__ = "user_subscription"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(64))
    marketplace: Mapped[str] = mapped_column(String(64))
    geo: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "category", "marketplace", "geo",
                         name="uq_subscription_combo"),
    )