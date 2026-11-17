"""Tabela de falhas de login para o limite de tentativas (achado 12).

Revision ID: 0007
Revises: 0006
"""
from alembic import op

from app.core.database import Base
import app.models  # noqa: F401

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["tentativas_login"].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tentativas_login")
