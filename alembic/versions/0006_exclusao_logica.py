"""Exclusão lógica de materiais e obras (achado 11).

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for tabela in ("materiais", "obras"):
        op.execute(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT true")
        op.execute(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS desativado_em TIMESTAMP")


def downgrade() -> None:
    for tabela in ("materiais", "obras"):
        op.execute(f"ALTER TABLE {tabela} DROP COLUMN IF EXISTS desativado_em")
        op.execute(f"ALTER TABLE {tabela} DROP COLUMN IF EXISTS ativo")
