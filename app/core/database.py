"""
Configuração da conexão assíncrona com o PostgreSQL usando SQLAlchemy.

A engine assíncrona (asyncpg) é o que permite que múltiplas requisições
concorrentes conversem com o banco sem bloquear o event loop do FastAPI.
"""
import logging
import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://estoque_user:estoque_pass@localhost:5432/estoque_db",
)

_pool_kwargs = {} if DATABASE_URL.startswith("sqlite") else {"pool_size": 20, "max_overflow": 10}
engine = create_async_engine(DATABASE_URL, echo=False, **_pool_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Dependency do FastAPI: entrega uma sessão por requisição."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_models():
    """Cria as tabelas no banco (uso em dev/demo; em produção use Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "postgresql":
            # create_all não altera tabelas existentes: adiciona as colunas novas de "obras"
            for ddl in (
                "ALTER TABLE obras ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'em_andamento'",
                "ALTER TABLE obras ADD COLUMN IF NOT EXISTS progresso INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE obras ADD COLUMN IF NOT EXISTS descricao_andamento TEXT",
                "ALTER TABLE obras ADD COLUMN IF NOT EXISTS finalizada_em TIMESTAMP",
                "ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS pedido_id VARCHAR(36)",
                "ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS solicitante_id INTEGER REFERENCES usuarios(id)",
                "ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS reivindicada_por VARCHAR(60)",
                "ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS reivindicada_em TIMESTAMP",
                "ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS tentativas INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS ultimo_erro TEXT",
                "ALTER TABLE requisicoes ADD COLUMN IF NOT EXISTS proxima_tentativa_em TIMESTAMP",
                "ALTER TABLE materiais ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT true",
                "ALTER TABLE materiais ADD COLUMN IF NOT EXISTS desativado_em TIMESTAMP",
                "ALTER TABLE obras ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT true",
                "ALTER TABLE obras ADD COLUMN IF NOT EXISTS desativado_em TIMESTAMP",
                "ALTER TABLE movimentacoes_estoque ADD COLUMN IF NOT EXISTS obra_id INTEGER REFERENCES obras(id)",
                "CREATE INDEX IF NOT EXISTS ix_movimentacoes_estoque_obra_id ON movimentacoes_estoque (obra_id)",
                "CREATE INDEX IF NOT EXISTS ix_requisicoes_obra_id ON requisicoes (obra_id)",
                "CREATE INDEX IF NOT EXISTS ix_requisicoes_material_id ON requisicoes (material_id)",
                "CREATE INDEX IF NOT EXISTS ix_requisicoes_status ON requisicoes (status)",
                "CREATE INDEX IF NOT EXISTS ix_requisicoes_criado_em ON requisicoes (criado_em)",
            ):
                await conn.exec_driver_sql(ddl)

            # banco criado antes do achado 8: as quantidades ainda são FLOAT. Quem converte é o Alembic (com a API parada)
            antigas = (await conn.exec_driver_sql(
                "SELECT count(*) FROM information_schema.columns WHERE data_type = 'double precision' AND "
                "(table_name, column_name) IN (('materiais','quantidade_estoque'), ('materiais','estoque_critico'), "
                "('requisicoes','quantidade_solicitada'), ('requisicoes','quantidade_atendida'), "
                "('movimentacoes_estoque','quantidade'), ('movimentacoes_estoque','saldo_apos'))"
            )).scalar()
            if antigas:
                logging.getLogger("database").warning(
                    "%s coluna(s) de quantidade ainda são FLOAT: rode `python -m alembic upgrade head` (veja o README).", antigas)
