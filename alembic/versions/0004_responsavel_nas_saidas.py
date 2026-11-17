"""Preenche o responsável (usuario_id) das saídas já existentes a partir da requisição ligada (RF16).

Revision ID: 0004
Revises: 0003

Só preenche onde está vazio e a requisição tem solicitante; o que não tem solicitante (registro antigo) continua nulo.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

PREENCHER = """
UPDATE movimentacoes_estoque m SET usuario_id = r.solicitante_id
FROM requisicoes r
WHERE m.requisicao_id = r.id AND m.tipo = 'saida' AND m.usuario_id IS NULL AND r.solicitante_id IS NOT NULL
"""


def upgrade() -> None:
    op.execute(PREENCHER)


def downgrade() -> None:
    pass        # não há como saber quais linhas estavam vazias antes; manter o responsável não causa dano
