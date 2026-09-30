"""niche: состояние обработки в ядре (core_status, search_query, core_error)

Revision ID: 8b4e1d2c7f3a
Revises: 5d2e8f1a9c3b
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa

revision = "8b4e1d2c7f3a"
down_revision = "5d2e8f1a9c3b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("niche", sa.Column("core_status", sa.String(32), nullable=True))
    op.add_column("niche", sa.Column("search_query", sa.Text(), nullable=True))
    op.add_column("niche", sa.Column("core_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("niche", "core_error")
    op.drop_column("niche", "search_query")
    op.drop_column("niche", "core_status")