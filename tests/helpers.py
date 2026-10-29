"""Fábricas e utilitários para os testes da camada de serviço (cada chamada usa sessão própria)."""
import asyncio
from datetime import datetime

from sqlalchemy import select

from app.models import Material, Obra, Requisicao, StatusRequisicao
from app.services.estoque_service import processar_requisicao

ABERTOS = (StatusRequisicao.PENDENTE, StatusRequisicao.EM_PROCESSAMENTO)


async def criar_material(Sessao, nome="Cabo", estoque=100.0, critico=0.0, unidade="m") -> int:
    async with Sessao() as db:
        m = Material(nome=nome, unidade=unidade, quantidade_estoque=estoque, estoque_critico=critico)
        db.add(m)
        await db.commit()
        return m.id


async def criar_obra(Sessao, nome="Obra", prioridade=5, parada=False, prazo: datetime | None = None) -> int:
    async with Sessao() as db:
        o = Obra(nome=nome, cidade="Recife", nivel_prioridade=prioridade,
                 parada_por_falta_material=parada, prazo_entrega=prazo)
        db.add(o)
        await db.commit()
        return o.id


async def criar_requisicoes(Sessao, obra_id: int, material_id: int, quantidades) -> list[str]:
    async with Sessao() as db:
        reqs = [Requisicao(obra_id=obra_id, material_id=material_id, quantidade_solicitada=q) for q in quantidades]
        db.add_all(reqs)
        await db.commit()
        return [r.id for r in reqs]


async def processar(Sessao, requisicao_id: str, worker: str = "w") -> Requisicao:
    """Processa uma requisição numa sessão independente (como cada worker faz)."""
    async with Sessao() as db:
        return await processar_requisicao(db, requisicao_id, worker)


async def estoque_de(Sessao, material_id: int) -> float:
    async with Sessao() as db:
        return (await db.execute(select(Material.quantidade_estoque).where(Material.id == material_id))).scalar_one()


async def requisicoes_de(Sessao, material_id: int | None = None) -> list[Requisicao]:
    async with Sessao() as db:
        q = select(Requisicao)
        if material_id is not None:
            q = q.where(Requisicao.material_id == material_id)
        return list((await db.execute(q)).scalars().all())


async def rodar_concorrente(coros, limite: int = 20):
    """Executa as corrotinas ao mesmo tempo, com no máximo `limite` conexões simultâneas
    (o Postgres do compose aceita 100 conexões; 20 = pool_size do app)."""
    sem = asyncio.Semaphore(limite)

    async def uma(c):
        async with sem:
            return await c

    return await asyncio.gather(*(uma(c) for c in coros))


async def esperar_processadas(Sessao, ids: list[str], timeout: float = 30.0):
    """Espera todas as requisições saírem de PENDENTE/EM_PROCESSAMENTO."""
    limite = asyncio.get_running_loop().time() + timeout
    while True:
        async with Sessao() as db:
            abertas = (await db.execute(
                select(Requisicao.id).where(Requisicao.id.in_(ids), Requisicao.status.in_(ABERTOS))
            )).all()
        if not abertas:
            return
        if asyncio.get_running_loop().time() > limite:
            raise TimeoutError(f"{len(abertas)} requisição(ões) ainda abertas após {timeout}s")
        await asyncio.sleep(0.05)


async def criar_requisicao(Sessao, obra_id: int, material_id: int, solicitada: float, *, atendida: float = 0.0,
                           status=StatusRequisicao.PENDENTE, criado_em: datetime | None = None,
                           processado_em: datetime | None = None, worker_id: str | None = None) -> str:
    """Requisição com todos os campos controlados (para montar cenários de relatório/diagnóstico)."""
    async with Sessao() as db:
        r = Requisicao(obra_id=obra_id, material_id=material_id, quantidade_solicitada=solicitada,
                       quantidade_atendida=atendida, status=status, processado_em=processado_em, worker_id=worker_id)
        if criado_em is not None:
            r.criado_em = criado_em
        db.add(r)
        await db.commit()
        return r.id


async def movimentacoes_de(Sessao, material_id: int):
    """Movimentações do material em ordem de id (= ordem em que o lock do material foi obtido)."""
    from app.models import MovimentacaoEstoque
    async with Sessao() as db:
        return list((await db.execute(
            select(MovimentacaoEstoque).where(MovimentacaoEstoque.material_id == material_id)
            .order_by(MovimentacaoEstoque.id))).scalars().all())
