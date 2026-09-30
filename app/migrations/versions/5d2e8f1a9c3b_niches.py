"""niches: user.is_internal, niche, user_niche; удаление ускоряющих индексов

Revision ID: 5d2e8f1a9c3b
Revises: 0024a1244427
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '5d2e8f1a9c3b'
down_revision: Union[str, Sequence[str], None] = '0024a1244427'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Ускоряющие индексы: при текущих объёмах не нужны.
# ix_user_username не трогаем — он обеспечивает уникальность логина.
SPEED_INDEXES = [
    ("ix_analysis_event_run_id", "analysis_event", ["run_id"]),
    ("ix_analysis_event_participant_event_id", "analysis_event_participant", ["event_id"]),
    ("ix_run_lookup", "analysis_run", ["category", "geo", "marketplace", "level", "algorithm"]),
    ("ix_market_curve_point_run_id", "market_curve_point", ["run_id"]),
    ("ix_user_subscription_user_id", "user_subscription", ["user_id"]),
]


def upgrade() -> None:
    # ---------- 1. Признак внутреннего клиента ----------
    # По умолчанию true на время тестирования; в продакшене поменяем на false.
    op.add_column("user", sa.Column("is_internal", sa.Boolean(), nullable=False,
                                    server_default=sa.text("true")))

    # ---------- 2. Ниши ----------
    # id уходит в ядро как external_id.
    op.create_table(
        "niche",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("marketplace", sa.String(64), nullable=False),
        sa.Column("geo", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("reference_url", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("submit_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime()),
        sa.CheckConstraint("description IS NOT NULL OR reference_url IS NOT NULL",
                           name="niche_input_check"),
        sa.CheckConstraint("status IN ('pending', 'submitted', 'deleted')",
                           name="niche_status_check"),
    )

    # ---------- 3. Связка пользователь — ниша ----------
    op.create_table(
        "user_niche",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("niche_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("niche.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ---------- 4. Удаление ускоряющих индексов ----------
    for name, table, _columns in SPEED_INDEXES:
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    for name, table, columns in SPEED_INDEXES:
        op.create_index(name, table, columns)
    op.drop_table("user_niche")
    op.drop_table("niche")
    op.drop_column("user", "is_internal")