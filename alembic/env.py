"""Alembic (async). Usa a MESMA DATABASE_URL da aplicação (variável de ambiente ou .env).

    python -m alembic upgrade head      # aplica as migrações pendentes
    python -m alembic current           # versão atual do banco
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import DATABASE_URL, Base
import app.models  # noqa: F401  (registra as tabelas em Base.metadata)

config = context.config
if config.config_file_name is not None and not config.attributes.get("programatico"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


LOCK_MIGRACAO = 727_274_001          # chave do lock consultivo do Postgres: só um processo migra por vez


def _migrar(conn):
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_MIGRACAO})
    conn.commit()                    # o lock é da CONEXÃO (sobrevive ao commit); deixa a transação limpa para o Alembic
    try:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if conn.in_transaction():
            conn.rollback()          # migração que falhou: nada parcial é gravado
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_MIGRACAO})
        conn.commit()


async def _online():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(_migrar)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_online())
