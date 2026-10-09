"""
Configuração da conexão assíncrona com o PostgreSQL usando SQLAlchemy.

A engine assíncrona (asyncpg) é o que permite que múltiplas requisições
concorrentes conversem com o banco sem bloquear o event loop do FastAPI.
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

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
            ):
                await conn.exec_driver_sql(ddl)
