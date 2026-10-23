"""search_point, weather_hourly: анализ поисковой выдачи и погода

Revision ID: c4a7e2f91d05
Revises: 8b4e1d2c7f3a
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4a7e2f91d05"
down_revision = "8b4e1d2c7f3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_point",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("marketplace", sa.String(64), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("geo", sa.String(128), nullable=False),
        sa.Column("run_at", sa.DateTime(), nullable=False),
        sa.Column("prev_run", sa.DateTime(), nullable=False),
        sa.Column("strata_n", sa.Integer(), nullable=False),
        sa.Column("strata_total", sa.Integer(), nullable=False),
        sa.Column("repeat_pct", sa.Float(), nullable=True),
        sa.Column("normal_pct", sa.Float(), nullable=True),
        sa.Column("band_low", sa.Float(), nullable=True),
        sa.Column("band_high", sa.Float(), nullable=True),
        sa.Column("z", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("temp_mean", sa.Float(), nullable=True),
        sa.Column("temp_now", sa.Float(), nullable=True),
        sa.Column("temp_min", sa.Float(), nullable=True),
        sa.Column("temp_max", sa.Float(), nullable=True),
        sa.Column("precip", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_set_id", "run_at", name="uq_search_point_run"),
    )
    op.create_table(
        "weather_hourly",
        sa.Column("geo", sa.String(128), nullable=False),
        sa.Column("time", sa.DateTime(), nullable=False),
        sa.Column("temp", sa.Float(), nullable=True),
        sa.Column("precip", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("geo", "time"),
    )


def downgrade() -> None:
    op.drop_table("weather_hourly")
    op.drop_table("search_point")