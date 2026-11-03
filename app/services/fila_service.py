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

Prioridade na ALOCAÇÃO (achado 14): tirar os itens da fila em ordem não basta, porque com vários workers
os itens correm juntos e quem chega primeiro ao lock do material leva o estoque. Por isso cada item retirado
recebe uma SENHA na fila do seu material (emitida dentro do mesmo lock da fila, na ordem de retirada = ordem
de prioridade). O worker só abre a transação quando o item anterior DO MESMO MATERIAL já TRAVOU o material
(não precisa esperar o commit): como o próximo só pede o lock depois do anterior, a ordem é estrita, e a preparação
do próximo (abrir conexão, BEGIN, ler a requisição) acontece enquanto o anterior ainda termina. Materiais diferentes
continuam em paralelo.
"""
import asyncio
import heapq
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Obra, Requisicao, StatusRequisicao
from app.services.estoque_service import processar_requisicao
from app.services.prioridade import chave_prioridade

logger = logging.getLogger("fila_service")

_counter = itertools.count()  # desempate estável para o heap


@dataclass(order=True)
class ItemFila:
    chave_prioridade: tuple = field(compare=True)
    sequencia: int = field(compare=True)
    requisicao_id: str = field(compare=False)
    material_id: int | None = field(compare=False, default=None)


class Senha:
    """A "vez" de um item na fila do seu material. Sem `material_id` a senha não bloqueia ninguém."""

    def __init__(self, fila: "FilaRequisicoes", material_id: int | None):
        self._fila = fila
        self._material_id = material_id
        self._minha = asyncio.Event()
        self._anterior: asyncio.Event | None = None
        if material_id is not None:
            self._anterior = fila._ultima.get(material_id)      # senha emitida logo antes, do mesmo material
            fila._ultima[material_id] = self._minha

    async def aguardar(self):
        """Espera o item anterior do mesmo material terminar (volta na hora se não houver)."""
        if self._anterior is not None:
            await self._anterior.wait()

    def liberar(self):
        """Avisa o próximo da fila do material (já travei o lock). Também no `finally` do worker, mesmo se o processamento falhar."""
        self._minha.set()
        if self._material_id is not None and self._fila._ultima.get(self._material_id) is self._minha:
            del self._fila._ultima[self._material_id]            # ninguém esperando atrás: não acumula memória


class FilaRequisicoes:
    """Fila de prioridade thread-safe para o event loop asyncio."""

    def __init__(self):
        self._heap: list[ItemFila] = []
        self._lock = asyncio.Lock()
        self._novo_item = asyncio.Event()
        self._ultima: dict[int, asyncio.Event] = {}              # material_id -> última senha emitida e ainda não liberada

    async def adicionar(
        self,
        requisicao_id: str,
        parada_por_falta_material: bool,
        nivel_prioridade: int,
        prazo_entrega: datetime | None,
        criado_em: datetime,
        material_id: int | None = None,
    ):
        # menor tupla = processado primeiro (regra em app/services/prioridade.py, compartilhada com o diagnóstico)
        chave = chave_prioridade(parada_por_falta_material, nivel_prioridade, prazo_entrega, criado_em)
        async with self._lock:
            heapq.heappush(self._heap, ItemFila(chave, next(_counter), requisicao_id, material_id))
            self._novo_item.set()

    def tamanho(self) -> int:
        return len(self._heap)

    async def proximo(self) -> str | None:
        """Próximo id da fila, sem senha (uso em testes e na análise de ordem de retirada)."""
        async with self._lock:
            if self._heap:
                return heapq.heappop(self._heap).requisicao_id
            self._novo_item.clear()
        await self._novo_item.wait()
        async with self._lock:
            if self._heap:
                return heapq.heappop(self._heap).requisicao_id
        return None

    def _retirar_com_senha(self) -> tuple[str, Senha]:
        item = heapq.heappop(self._heap)
        return item.requisicao_id, Senha(self, item.material_id)   # chamado DENTRO do lock da fila: ordem da senha = ordem de retirada

    async def proximo_com_senha(self) -> tuple[str | None, Senha | None]:
        """Como `proximo`, mas devolve também a Senha do item (quem usa precisa chamar `senha.liberar()`)."""
        async with self._lock:
            if self._heap:
                return self._retirar_com_senha()
            self._novo_item.clear()
        await self._novo_item.wait()
        async with self._lock:
            if self._heap:
                return self._retirar_com_senha()
        return None, None


fila_global = FilaRequisicoes()


async def worker_loop(worker_id: str, fila: FilaRequisicoes):
    """Loop infinito: cada worker pega a próxima requisição e processa, respeitando a vez no seu material."""
    while True:
        requisicao_id, senha = await fila.proximo_com_senha()
        if requisicao_id is None:
            continue
        try:
            await senha.aguardar()          # prioridade na alocação: espera o item anterior do mesmo material travar o lock
            async with AsyncSessionLocal() as db:
                resultado = await processar_requisicao(db, requisicao_id, worker_id, ao_travar_material=senha.liberar)
                logger.info(
                    "[%s] requisicao=%s status=%s atendida=%s",
                    worker_id, requisicao_id, resultado.status, resultado.quantidade_atendida,
                )
        except Exception:
            logger.exception("Erro processando requisicao %s no %s", requisicao_id, worker_id)
            await _marcar_erro(requisicao_id, worker_id)
        finally:
            senha.liberar()                 # sempre: senão os próximos do mesmo material esperariam para sempre


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
                material_id=req.material_id,
            )


async def iniciar_workers(n_workers: int = 3) -> list[asyncio.Task]:
    """Sobe N workers concorrentes consumindo da fila global."""
    return [
        asyncio.create_task(worker_loop(f"worker-{i+1}", fila_global))
        for i in range(n_workers)
    ]
