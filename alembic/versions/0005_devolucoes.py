"""Devoluções (RF15): obra de origem nas movimentações de estoque.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE movimentacoes_estoque ADD COLUMN IF NOT EXISTS obra_id INTEGER REFERENCES obras(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_movimentacoes_estoque_obra_id ON movimentacoes_estoque (obra_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_movimentacoes_estoque_obra_id")
    op.execute("ALTER TABLE movimentacoes_estoque DROP COLUMN IF EXISTS obra_id")
