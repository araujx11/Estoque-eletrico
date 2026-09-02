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

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=20, max_overflow=10)

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
