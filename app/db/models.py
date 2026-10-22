# ---------- Таблицы результатов аналитики (своя база analytics) ----------
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
import app.users.models  # noqa: F401
import app.niches.models  # noqa: F401


class AnalysisRun(Base):
    """Один расчёт одного среза: сет + уровень + алгоритм.
    Ссылки на сет и страты хранятся как UUID без внешнего ключа - база чужая."""
    __tablename__ = "analysis_run"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    # откуда взяты данные
    source_set_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    marketplace: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(64))
    geo: Mapped[str] = mapped_column(String(128))

    # что считали
    level: Mapped[str] = mapped_column(String(16))        # category | subcategory | item
    level_key: Mapped[str] = mapped_column(String(256))   # название среза
    algorithm: Mapped[str] = mapped_column(String(16))    # EWMA | MACD

    # на какой выборке
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    grid_point_at: Mapped[datetime] = mapped_column(DateTime)   # точка сетки, за которую расчёт
    data_from: Mapped[datetime | None] = mapped_column(DateTime)
    data_to: Mapped[datetime | None] = mapped_column(DateTime)
    n_cards: Mapped[int] = mapped_column(Integer)
    total_quota: Mapped[int] = mapped_column(Integer)
    deficit_share: Mapped[float] = mapped_column(Float, default=0.0)
    scale_factor: Mapped[float] = mapped_column(Float, default=1.0)

    # результат
    sufficient: Mapped[bool] = mapped_column(Boolean, default=True)
    insufficient_reason: Mapped[str | None] = mapped_column(Text)
    warning: Mapped[str | None] = mapped_column(Text)
    params_json: Mapped[str | None] = mapped_column(Text)   # параметры алгоритма на момент расчёта

    events: Mapped[list["AnalysisEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")
    curve_points: Mapped[list["MarketCurvePoint"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_set_id", "level", "level_key", "algorithm", "grid_point_at",
                         name="uq_run_snapshot_point"),
    )


class AnalysisEvent(Base):
    """Одно обнаруженное событие. Величина и скорость хранятся в ЛОГ-единицах:
    перевод в проценты - на выводе, чтобы не терять точность при агрегации."""
    __tablename__ = "analysis_event"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_run.id", ondelete="CASCADE"))

    direction: Mapped[str] = mapped_column(String(8))     # down | up
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    peak_at: Mapped[datetime] = mapped_column(DateTime)
    duration_days: Mapped[float] = mapped_column(Float)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)   # событие не завершено

    participation_pct: Mapped[float] = mapped_column(Float)
    participation_ci_low: Mapped[float | None] = mapped_column(Float)
    participation_ci_high: Mapped[float | None] = mapped_column(Float)

    participants: Mapped[list["AnalysisEventParticipant"]] = relationship(
        back_populates="event", cascade="all, delete-orphan")

    magnitude_mean_log: Mapped[float | None] = mapped_column(Float)
    magnitude_peak_log: Mapped[float | None] = mapped_column(Float)
    magnitude_ci_low_log: Mapped[float | None] = mapped_column(Float)
    magnitude_ci_high_log: Mapped[float | None] = mapped_column(Float)

    speed_mean_log: Mapped[float | None] = mapped_column(Float)
    speed_peak_log: Mapped[float | None] = mapped_column(Float)
    speed_ci_low_log: Mapped[float | None] = mapped_column(Float)
    speed_ci_high_log: Mapped[float | None] = mapped_column(Float)

    run: Mapped[AnalysisRun] = relationship(back_populates="events")


class MarketCurvePoint(Base):
    """Средний сглаженный уровень рынка в точке сетки - основа графика."""
    __tablename__ = "market_curve_point"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_run.id", ondelete="CASCADE"))
    parsed_at: Mapped[datetime] = mapped_column(DateTime)
    level_value: Mapped[float | None] = mapped_column(Float)   # log-уровень

    run: Mapped[AnalysisRun] = relationship(back_populates="curve_points")


class AnalysisEventParticipant(Base):
    """Карточка, участвовавшая в конкретном событии - для кнопки
    "Посмотреть участников" на фронте."""
    __tablename__ = "analysis_event_participant"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_event.id", ondelete="CASCADE"))
    card_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sku: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))

    event: Mapped["AnalysisEvent"] = relationship(back_populates="participants")

    __table_args__ = (
        UniqueConstraint("event_id", "card_id", name="uq_event_participant"),
    )