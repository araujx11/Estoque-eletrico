"""Quantidades de FLOAT para NUMERIC(14,3) (achado 8).

Revision ID: 0002
Revises: 0001

Converte as 6 colunas de quantidade SEM apagar dados (`ALTER COLUMN ... TYPE NUMERIC USING ROUND(col::numeric, 3)`).
É idempotente: colunas que já são NUMERIC (banco novo) são ignoradas. Se algum valor antigo tiver mais de 3 casas
(erro de ponto flutuante, ex.: 0.30000000000000004), ele é arredondado para 3 casas; nada é perdido além do ruído.
O ALTER reescreve a tabela e trava as linhas: rode com a API parada.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

COLUNAS = (
    ("materiais", "quantidade_estoque"), ("materiais", "estoque_critico"),
    ("requisicoes", "quantidade_solicitada"), ("requisicoes", "quantidade_atendida"),
    ("movimentacoes_estoque", "quantidade"), ("movimentacoes_estoque", "saldo_apos"),
)


def _tipo_atual(conn, tabela: str, coluna: str) -> str | None:
    return conn.execute(sa.text(
        "SELECT data_type FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
    ), {"t": tabela, "c": coluna}).scalar()


def upgrade() -> None:
    conn = op.get_bind()
    for tabela, coluna in COLUNAS:
        if _tipo_atual(conn, tabela, coluna) in ("double precision", "real"):
            op.execute(
                f'ALTER TABLE {tabela} ALTER COLUMN {coluna} TYPE NUMERIC(14,3) USING ROUND({coluna}::numeric, 3)'
            )


def downgrade() -> None:
    conn = op.get_bind()
    for tabela, coluna in COLUNAS:
        if _tipo_atual(conn, tabela, coluna) == "numeric":
            op.execute(f"ALTER TABLE {tabela} ALTER COLUMN {coluna} TYPE DOUBLE PRECISION USING {coluna}::double precision")
