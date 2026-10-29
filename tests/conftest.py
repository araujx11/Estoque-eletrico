"""
Infraestrutura dos testes.

* Banco: `estoque_test` no mesmo PostgreSQL do docker-compose (TEST_DATABASE_URL para trocar).
  É criado automaticamente, e o schema é recriado a cada sessão. O banco de desenvolvimento
  nunca é usado: validar_url_descartavel() recusa nomes que não terminem em _test/_bench.
* Isolamento: TRUNCATE de todas as tabelas antes de cada teste; nenhum teste depende de dados prévios.
* Event loop: um único loop para a sessão (ver pytest.ini). O engine asyncpg do app fica preso ao
  loop que o criou; compartilhar o loop evita "attached to a different loop".
* Camada de serviço: engine próprio com pool de 20 conexões; DDL/TRUNCATE usam NullPool.

As variáveis de ambiente precisam ser definidas ANTES de importar a aplicação.
"""
import os

from devtools.dbutil import garantir_banco, limpar_dados, recriar_schema, url_teste, validar_url_descartavel

TEST_URL = validar_url_descartavel(url_teste())
os.environ["DATABASE_URL"] = TEST_URL          # o app (e os workers) passam a usar o banco de teste
os.environ["SECRET_KEY"] = "chave-de-teste-com-mais-de-32-caracteres-ok"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.database import Base, engine as app_engine  # noqa: E402
from app.core.security import criar_token, hash_senha  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Perfil, Usuario  # noqa: E402
from app.services.fila_service import fila_global  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    await garantir_banco(TEST_URL)
    await recriar_schema(TEST_URL, Base.metadata)
    yield
    await app_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _banco_limpo(_schema):
    """Cada teste começa com as tabelas vazias e a fila global (em memória) vazia."""
    fila_global._heap.clear()
    await limpar_dados(TEST_URL, Base.metadata)
    yield
    fila_global._heap.clear()


@pytest_asyncio.fixture(scope="session")
async def SessionTeste():
    """Fábrica de sessões independentes para testar a camada de serviço.

    Pool de 20 conexões (= pool_size do app): seguro porque o loop é único na sessão, e ~10x mais
    rápido que NullPool, onde cada tarefa pagaria uma autenticação nova no Postgres."""
    eng = create_async_engine(TEST_URL, pool_size=20, max_overflow=0)
    yield async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
    await eng.dispose()


# ---------- API ----------

SENHAS = {"gestor": "senha-do-gestor", "almox": "senha-12345", "eng": "senha-12345"}


@pytest_asyncio.fixture(scope="session")
async def _hashes():
    # bcrypt é lento de propósito: calcula uma vez por sessão e reaproveita nos usuários de cada teste
    return {k: await hash_senha(v) for k, v in SENHAS.items()}


@pytest_asyncio.fixture
async def client():
    # ASGITransport não executa o lifespan: sem workers (testes que precisam deles os iniciam)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://teste") as c:
        yield c


def _bearer(usuario_id: int) -> dict:
    return {"Authorization": f"Bearer {criar_token(usuario_id)}"}


@pytest_asyncio.fixture
async def contas(client, SessionTeste, _hashes):
    """Gestor, almoxarife e engenheiro (obra A) + obras A/B + um material, prontos a cada teste."""
    async with SessionTeste() as db:
        gestor = Usuario(nome="Gestor", email="gestor@teste.com", senha_hash=_hashes["gestor"], perfil=Perfil.GESTOR)
        almox = Usuario(nome="Alma", email="almox@teste.com", senha_hash=_hashes["almox"], perfil=Perfil.ALMOXARIFE)
        db.add_all([gestor, almox])
        await db.commit()                      # a API roda noutra sessão: precisa enxergar o gestor
        h_gestor = _bearer(gestor.id)
        obra_a = (await client.post("/obras", json={"nome": "Obra A", "cidade": "Recife"}, headers=h_gestor)).json()
        obra_b = (await client.post("/obras", json={"nome": "Obra B", "cidade": "Olinda"}, headers=h_gestor)).json()
        eng = Usuario(nome="Eng A", email="eng@teste.com", senha_hash=_hashes["eng"],
                      perfil=Perfil.ENGENHEIRO, obra_id=obra_a["id"])
        db.add(eng)
        await db.commit()
        h_eng, h_almox = _bearer(eng.id), _bearer(almox.id)
    material = (await client.post("/materiais", json={
        "nome": "Cabo 10mm2", "unidade": "m", "quantidade_estoque": 100, "estoque_critico": 10,
    }, headers=h_gestor)).json()
    return {"gestor": h_gestor, "almox": h_almox, "eng": h_eng,
            "obra_a": obra_a, "obra_b": obra_b, "material": material}


@pytest.fixture
def gestor(contas):
    return contas["gestor"]


@pytest.fixture
def almox(contas):
    return contas["almox"]


@pytest.fixture
def eng(contas):
    return contas["eng"]


@pytest.fixture
def dados(contas):
    return {k: contas[k] for k in ("obra_a", "obra_b", "material")}
