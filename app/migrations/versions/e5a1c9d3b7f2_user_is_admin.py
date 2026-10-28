"""user.is_admin: администратор админки задания (один человек, ставится вручную)

Revision ID: e5a1c9d3b7f2
Revises: d81b5c3e6a27
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "e5a1c9d3b7f2"
down_revision = "d81b5c3e6a27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("is_admin", sa.Boolean(), nullable=False,
                                    server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("user", "is_admin")