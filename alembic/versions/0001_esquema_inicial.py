"""Esquema inicial (ponto de partida do Alembic).

Revision ID: 0001
Revises:

Cria as tabelas que ainda não existem (`checkfirst`) com o esquema do código. Em um banco que já existia
(criado pelo `init_models()` antes do Alembic), não altera nada: as colunas de quantidade ainda em FLOAT
são convertidas pela migração 0002.
"""
from alembic import op

from app.core.database import Base
import app.models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError("A migração inicial não é revertida (apagaria todos os dados).")
