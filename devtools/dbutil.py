"""
Utilitários para os bancos de teste e benchmark (nunca o banco de desenvolvimento).

Os bancos `estoque_test` e `estoque_bench` ficam no mesmo PostgreSQL do docker-compose e são
criados automaticamente. Por segurança, só aceitamos nomes terminados em `_test` ou `_bench`.
"""
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

BASE = "postgresql+asyncpg://estoque_user:estoque_pass@localhost:5432"
TEST_URL_PADRAO = f"{BASE}/estoque_test"
BENCH_URL_PADRAO = f"{BASE}/estoque_bench"

SUFIXOS_PERMITIDOS = ("_test", "_bench")


def url_teste() -> str:
    return os.getenv("TEST_DATABASE_URL", TEST_URL_PADRAO)


def url_bench() -> str:
    return os.getenv("BENCH_DATABASE_URL", BENCH_URL_PADRAO)


def validar_url_descartavel(url: str) -> str:
    """Recusa URLs que não sejam de banco de teste/benchmark (protege o banco de desenvolvimento)."""
    nome = make_url(url).database or ""
    if not nome.endswith(SUFIXOS_PERMITIDOS):
        raise RuntimeError(
            f"Banco '{nome}' recusado: testes/benchmark só rodam em bancos cujo nome termina em "
            f"{' ou '.join(SUFIXOS_PERMITIDOS)} (o banco de desenvolvimento nunca é usado)."
        )
    return url


async def garantir_banco(url: str) -> None:
    """Cria o banco da URL se ele não existir (conecta no banco 'postgres' com AUTOCOMMIT)."""
    validar_url_descartavel(url)
    alvo = make_url(url)
    admin = create_async_engine(
        alvo.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with admin.connect() as conn:
            existe = (await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": alvo.database}
            )).scalar()
            if not existe:
                # identificador já validado pelo sufixo; CREATE DATABASE não aceita parâmetros
                await conn.execute(text(f'CREATE DATABASE "{alvo.database}"'))
    finally:
        await admin.dispose()


async def recriar_schema(url: str, metadata) -> None:
    """drop_all + create_all no banco descartável (começa cada sessão com o schema atual)."""
    validar_url_descartavel(url)
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(metadata.drop_all)
            await conn.run_sync(metadata.create_all)
    finally:
        await eng.dispose()


async def limpar_dados(url: str, metadata) -> None:
    """TRUNCATE de todas as tabelas (reinicia os ids). Mais rápido que recriar o schema."""
    validar_url_descartavel(url)
    tabelas = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))
    finally:
        await eng.dispose()
