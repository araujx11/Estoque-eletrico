"""
Fila de requisições + pool de workers.

A fila em si NÃO é o que garante a integridade do estoque (isso é papel
do lock no banco, em estoque_service.py). A fila é responsável por:

  1. Organizar a ORDEM de processamento (priorização).
  2. Permitir que N workers consumam requisições concorrentemente,
     gerando o cenário real de disputa pelo mesmo material.

Prioridade (cascata de critérios, do mais para o menos importante):
  1. Obra parada por falta de material (urgência operacional)
  2. Nível de prioridade da obra (1 = mais urgente)
  3. Prazo de entrega mais próximo
  4. Requisição mais antiga (FIFO como desempate final)
"""
import asyncio
import heapq
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Obra, Requisicao, StatusRequisicao
from app.services.estoque_service import processar_requisicao

logger = logging.getLogger("fila_service")

_counter = itertools.count()  # desempate estável para o heap


@dataclass(order=True)
class ItemFila:
    chave_prioridade: tuple = field(compare=True)
    sequencia: int = field(compare=True)
    requisicao_id: str = field(compare=False)


class FilaRequisicoes:
    """Fila de prioridade thread-safe para o event loop asyncio."""

    def __init__(self):
        self._heap: list[ItemFila] = []
        self._lock = asyncio.Lock()
        self._novo_item = asyncio.Event()

    async def adicionar(
        self,
        requisicao_id: str,
        parada_por_falta_material: bool,
        nivel_prioridade: int,
        prazo_entrega: datetime | None,
        criado_em: datetime,
    ):
        # menor tupla = processado primeiro
        chave = (
            0 if not parada_por_falta_material else -1,  # obra parada vem primeiro
            nivel_prioridade,
            prazo_entrega or datetime.max,
            criado_em,
        )
        if criado_em.tzinfo is not None:
            criado_em = criado_em.astimezone(timezone.utc).replace(tzinfo=None)
        if prazo_entrega is not None and prazo_entrega.tzinfo is not None:
            prazo_entrega = prazo_entrega.astimezone(timezone.utc).replace(tzinfo=None)
        chave = (chave[0], chave[1], prazo_entrega or datetime.max, criado_em)
        async with self._lock:
            heapq.heappush(self._heap, ItemFila(chave, next(_counter), requisicao_id))
            self._novo_item.set()

    def tamanho(self) -> int:
        return len(self._heap)

    async def proximo(self) -> str | None:
        async with self._lock:
            if self._heap:
                return heapq.heappop(self._heap).requisicao_id
            self._novo_item.clear()
        await self._novo_item.wait()
        async with self._lock:
            if self._heap:
                return heapq.heappop(self._heap).requisicao_id
        return None


fila_global = FilaRequisicoes()


async def worker_loop(worker_id: str, fila: FilaRequisicoes):
    """Loop infinito: cada worker pega a próxima requisição e processa."""
    while True:
        requisicao_id = await fila.proximo()
        if requisicao_id is None:
            continue
        try:
            async with AsyncSessionLocal() as db:
                resultado = await processar_requisicao(db, requisicao_id, worker_id)
                logger.info(
                    "[%s] requisicao=%s status=%s atendida=%s",
                    worker_id, requisicao_id, resultado.status, resultado.quantidade_atendida,
                )
        except Exception:
            logger.exception("Erro processando requisicao %s no %s", requisicao_id, worker_id)
            await _marcar_erro(requisicao_id, worker_id)


async def _marcar_erro(requisicao_id: str, worker_id: str):
    try:
        async with AsyncSessionLocal() as db:
            req = await db.get(Requisicao, requisicao_id)
            if req is not None:
                req.status = StatusRequisicao.ERRO
                req.worker_id = worker_id
                await db.commit()
    except Exception:
        logger.exception("Falha ao marcar requisicao %s como erro", requisicao_id)


async def reenfileirar_pendentes():
    """Após restart, recoloca na fila o que ficou PENDENTE/EM_PROCESSAMENTO (a fila é em memória)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Requisicao, Obra)
            .join(Obra, Obra.id == Requisicao.obra_id)
            .where(Requisicao.status.in_(
                [StatusRequisicao.PENDENTE, StatusRequisicao.EM_PROCESSAMENTO]
            ))
        )
        for req, obra in result.all():
            await fila_global.adicionar(
                requisicao_id=req.id,
                parada_por_falta_material=obra.parada_por_falta_material,
                nivel_prioridade=obra.nivel_prioridade,
                prazo_entrega=obra.prazo_entrega,
                criado_em=req.criado_em,
            )


async def iniciar_workers(n_workers: int = 3) -> list[asyncio.Task]:
    """Sobe N workers concorrentes consumindo da fila global."""
    return [
        asyncio.create_task(worker_loop(f"worker-{i+1}", fila_global))
        for i in range(n_workers)
    ]
