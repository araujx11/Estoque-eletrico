"""Colunas da fila multiprocesso (achado 2) e do reprocessamento de erros (achado 3).

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

COLUNAS = (
    ("reivindicada_por", "VARCHAR(60)"),
    ("reivindicada_em", "TIMESTAMP"),
    ("tentativas", "INTEGER NOT NULL DEFAULT 0"),
    ("ultimo_erro", "TEXT"),
    ("proxima_tentativa_em", "TIMESTAMP"),
)


def upgrade() -> None:
    for nome, tipo in COLUNAS:                       # IF NOT EXISTS: o init_models() de bancos antigos já pode tê-las criado
        op.execute(f"ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS {nome} {tipo}")


def downgrade() -> None:
    for nome, _ in COLUNAS:
        op.execute(f"ALTER TABLE requisicoes DROP COLUMN IF EXISTS {nome}")
