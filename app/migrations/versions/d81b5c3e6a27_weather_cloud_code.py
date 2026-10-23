"""weather_hourly: облачность и код погоды WMO; кеш очищается для перекачки

Revision ID: d81b5c3e6a27
Revises: c4a7e2f91d05
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = "d81b5c3e6a27"
down_revision = "c4a7e2f91d05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("weather_hourly", sa.Column("cloud", sa.Float(), nullable=True))
    op.add_column("weather_hourly", sa.Column("code", sa.Integer(), nullable=True))
    # старые строки без облачности и кода - очистить, при следующем расчёте кеш перекачается
    op.execute("DELETE FROM weather_hourly")


def downgrade() -> None:
    op.drop_column("weather_hourly", "code")
    op.drop_column("weather_hourly", "cloud")