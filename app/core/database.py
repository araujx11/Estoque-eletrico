"""
Configuração da conexão assíncrona com o PostgreSQL usando SQLAlchemy.

A engine assíncrona (asyncpg) é o que permite que múltiplas requisições
concorrentes conversem com o banco sem bloquear o event loop do FastAPI.
"""
import asyncio
import logging
import os
from pathlib import Path

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


def _config_alembic():
    from alembic.config import Config
    raiz = Path(__file__).resolve().parents[2]
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    cfg.attributes["programatico"] = True          # env.py não mexe na configuração de logs da aplicação
    return cfg


def _migrar_sync():
    from alembic import command
    command.upgrade(_config_alembic(), "head")


async def init_models():
    """Prepara o banco na partida da aplicação: aplica as migrações do Alembic (`alembic upgrade head`).

    * Banco novo: a migração 0001 cria o esquema atual. Banco antigo: as migrações aplicam só o que falta (sem apagar dados).
    * Vários processos subindo juntos: o env.py usa um lock do Postgres, então só um migra por vez.
    * `AUTO_MIGRAR=0` desliga (quem prefere rodar `python -m alembic upgrade head` à mão antes de subir a API).
    * Fora do PostgreSQL (SQLite em experimentos) não há migrações: só `create_all`.
    """
    if engine.dialect.name != "postgresql":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    if os.getenv("AUTO_MIGRAR", "1") == "0":
        logging.getLogger("database").warning("AUTO_MIGRAR=0: as migrações não foram aplicadas (rode `python -m alembic upgrade head`).")
        return
    await asyncio.to_thread(_migrar_sync)          # o Alembic usa o próprio event loop (asyncio.run) numa thread à parte
