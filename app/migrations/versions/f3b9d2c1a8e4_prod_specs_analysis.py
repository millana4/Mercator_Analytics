"""prod_specs_analysis: рекомендации по закупкам, один анализ на страту

Revision ID: f3b9d2c1a8e4
Revises: e5a1c9d3b7f2
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f3b9d2c1a8e4"
down_revision = "e5a1c9d3b7f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Анализ страты: отобранные карточки (sku по порядку), отпечаток их состава и результат
    # шага 2 (группы трендовых предметов с описаниями). Одна запись на страту: новый анализ
    # заменяет прежний. Ссылки на сет и страту - UUID без внешнего ключа, база чужая.
    # copied_from_stratum - страта, из которой скопирован результат (состав совпал
    # после смены сета); пусто, если считалось заново.
    op.create_table(
        "prod_specs_analysis",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("marketplace", sa.String(64), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("geo", sa.String(128), nullable=False),
        sa.Column("stratum_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stratum_label", sa.String(512), nullable=False),
        sa.Column("selection_hash", sa.String(64), nullable=False),
        sa.Column("skus", postgresql.JSONB(), nullable=False),
        sa.Column("n_cards", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("copied_from_stratum", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stratum_id", name="uq_prod_specs_analysis_stratum"),
    )


def downgrade() -> None:
    op.drop_table("prod_specs_analysis")