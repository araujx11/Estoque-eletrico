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

Vários processos (achado 2): com `uvicorn --workers N` cada processo tem a sua fila em memória. Para não enfileirar
a mesma requisição em todos, quem a põe na fila antes REIVINDICA a requisição no banco, de forma atômica:
`UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING`. Linhas já travadas por outro processo são
puladas (não espera) e as já reivindicadas deixam de ser candidatas, então cada requisição vira item de UMA fila só.
A reivindicação é um prazo (lease, `FILA_LEASE_S`): se o dono morrer, outro processo retoma depois que o prazo vence.
Isto só evita trabalho duplicado: a CORREÇÃO do estoque continua sendo do lock + idempotência (RN14) em
estoque_service. Se duas filas ainda assim processarem a mesma requisição (prazo vencido, requisição criada pela API
enquanto outro processo reenfileira), a segunda a encontra resolvida e não debita de novo.

Erros (achado 3): `_marcar_erro` conta a tentativa e guarda o erro; uma rotina periódica (`reprocessar_erros_loop`)
reivindica as requisições em ERRO que ainda têm tentativas e já passaram do backoff e as recoloca na fila. Passou do
limite (`ERRO_MAX_TENTATIVAS`): ERRO definitivo, com registro na auditoria e visível no diagnóstico.
"""
import asyncio
import heapq
import itertools
import logging
import os
import socket
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update

from app.core.database import AsyncSessionLocal
from app.models import Obra, Requisicao, StatusRequisicao
from app.services import auditoria_service
from app.services.estoque_service import RESOLVIDAS, processar_requisicao
from app.services.prioridade import chave_prioridade

logger = logging.getLogger("fila_service")

_counter = itertools.count()  # desempate estável para o heap

# identifica ESTE processo nas reivindicações (host:pid:aleatório); o sufixo evita colisão se o pid for reaproveitado
INSTANCIA_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"[:60]


# Configuração por variáveis de ambiente, lidas a cada uso (dá para ajustar sem reiniciar os testes)
def _env_num(nome: str, padrao: float) -> float:
    try:
        return float(os.getenv(nome, padrao))
    except ValueError:
        return padrao


def lease_s() -> float:
    """Quanto tempo uma reivindicação vale antes de outro processo poder retomá-la."""
    return _env_num("FILA_LEASE_S", 300)


def max_tentativas() -> int:
    """Total de execuções com falha aceitas antes de a requisição ficar em ERRO definitivo."""
    return max(1, int(_env_num("ERRO_MAX_TENTATIVAS", 3)))


def backoff_base_s() -> float:
    """Espera após a 1ª falha; dobra a cada nova falha (5 s, 10 s, 20 s...)."""
    return max(0.0, _env_num("ERRO_BACKOFF_BASE_S", 5))


def intervalo_reprocessar_s() -> float:
    """De quanto em quanto tempo a rotina procura erros para reprocessar (0 = desligada)."""
    return _env_num("ERRO_REPROCESSAR_INTERVALO_S", 30)


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)        # naive em UTC, como o resto do projeto


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
    """Registra a falha: ERRO + contador + último erro. Chamada de dentro do `except` do worker (lê a exceção em curso)."""
    erro = sys.exception()
    texto = (f"{type(erro).__name__}: {erro}" if erro is not None else "erro desconhecido")[:500]
    try:
        async with AsyncSessionLocal() as db, db.begin():
            req = (await db.execute(
                select(Requisicao).where(Requisicao.id == requisicao_id).with_for_update()
            )).scalar_one_or_none()
            if req is None:
                return
            if req.status in RESOLVIDAS:
                # a falha veio DEPOIS do commit (ex.: conexão caiu no refresh): o débito já foi feito. Marcar ERRO aqui
                # faria o reprocessamento debitar de novo, então a requisição fica como está.
                logger.warning("requisicao %s já estava %s quando falhou: não vira ERRO", requisicao_id, req.status.value)
                return
            req.status = StatusRequisicao.ERRO
            req.worker_id = worker_id
            req.tentativas = (req.tentativas or 0) + 1
            req.ultimo_erro = texto
            req.reivindicada_em = None
            definitivo = req.tentativas >= max_tentativas()
            req.proxima_tentativa_em = None if definitivo else _agora() + timedelta(
                seconds=backoff_base_s() * 2 ** (req.tentativas - 1))
            if definitivo:
                auditoria_service.registrar(db, None, "requisicao_erro_definitivo", "requisicao", req.id, {
                    "material_id": req.material_id, "obra_id": req.obra_id, "tentativas": req.tentativas,
                    "ultimo_erro": texto,
                })
    except Exception:
        logger.exception("Falha ao marcar requisicao %s como erro", requisicao_id)


async def reivindicar_e_enfileirar(
    fila: "FilaRequisicoes", sessao=None, instancia: str | None = None, com_erros: bool = False,
) -> int:
    """Reivindica de forma atômica as requisições que ainda não são de ninguém e as põe na `fila`. Devolve quantas.

    Candidatas: PENDENTE; EM_PROCESSAMENTO com prazo vencido (dono caiu) ou sem dono (registro antigo); e, com
    `com_erros`, ERRO com tentativas sobrando e backoff vencido. `FOR UPDATE SKIP LOCKED` faz dois processos que
    reivindicam ao mesmo tempo receberem conjuntos disjuntos (nenhum espera o outro). A transação só contém o UPDATE,
    então os locks duram milissegundos e não interferem no lock do material.
    """
    sessao = sessao or AsyncSessionLocal
    instancia = instancia or INSTANCIA_ID
    agora = _agora()
    prazo_vencido = or_(Requisicao.reivindicada_em.is_(None), Requisicao.reivindicada_em < agora - timedelta(seconds=lease_s()))
    candidatas = [
        Requisicao.status == StatusRequisicao.PENDENTE,
        and_(Requisicao.status == StatusRequisicao.EM_PROCESSAMENTO, prazo_vencido),
    ]
    if com_erros:
        candidatas.append(and_(
            Requisicao.status == StatusRequisicao.ERRO,
            Requisicao.tentativas < max_tentativas(),
            or_(Requisicao.proxima_tentativa_em.is_(None), Requisicao.proxima_tentativa_em <= agora),
        ))
    alvo = select(Requisicao.id).where(or_(*candidatas)).with_for_update(skip_locked=True)
    async with sessao() as db, db.begin():
        reivindicadas = (await db.execute(
            update(Requisicao).where(Requisicao.id.in_(alvo))
            .values(status=StatusRequisicao.EM_PROCESSAMENTO, reivindicada_por=instancia, reivindicada_em=agora)
            .returning(Requisicao.id, Requisicao.obra_id, Requisicao.material_id, Requisicao.criado_em)
            .execution_options(synchronize_session=False)
        )).all()
        obras = {}
        if reivindicadas:
            obras = {o.id: o for o in (await db.execute(
                select(Obra).where(Obra.id.in_({r.obra_id for r in reivindicadas})))).scalars().all()}
    for r in reivindicadas:                                   # fora da transação: o commit já tornou a reivindicação definitiva
        obra = obras[r.obra_id]
        await fila.adicionar(
            requisicao_id=r.id,
            parada_por_falta_material=obra.parada_por_falta_material,
            nivel_prioridade=obra.nivel_prioridade,
            prazo_entrega=obra.prazo_entrega,
            criado_em=r.criado_em,
            material_id=r.material_id,
        )
    return len(reivindicadas)


async def reenfileirar_pendentes(fila: "FilaRequisicoes | None" = None, sessao=None, instancia: str | None = None) -> int:
    """Na partida (a fila é em memória): reivindica e recoloca na fila o que ficou aberto. Seguro com vários processos."""
    return await reivindicar_e_enfileirar(fila or fila_global, sessao, instancia)


async def reprocessar_erros(fila: "FilaRequisicoes | None" = None, sessao=None, instancia: str | None = None) -> int:
    """Uma passada da rotina de reprocessamento: reivindica o que está em ERRO com direito a nova tentativa
    (e requisições abandonadas por processos que caíram) e recoloca na fila."""
    return await reivindicar_e_enfileirar(fila or fila_global, sessao, instancia, com_erros=True)


async def reprocessar_erros_loop(fila: "FilaRequisicoes | None" = None):
    """Rotina periódica do processo (intervalo em `ERRO_REPROCESSAR_INTERVALO_S`; 0 desliga)."""
    while True:
        intervalo = intervalo_reprocessar_s()
        if intervalo <= 0:
            return
        await asyncio.sleep(intervalo)
        try:
            n = await reprocessar_erros(fila)
            if n:
                logger.info("reprocessamento: %s requisicao(oes) recolocada(s) na fila", n)
        except Exception:
            logger.exception("Falha na rotina de reprocessamento de erros")


async def iniciar_workers(n_workers: int = 3) -> list[asyncio.Task]:
    """Sobe N workers concorrentes consumindo da fila global, mais a rotina de reprocessamento de erros."""
    tarefas = [
        asyncio.create_task(worker_loop(f"worker-{i+1}", fila_global))
        for i in range(n_workers)
    ]
    if n_workers > 0:                       # sem workers (N_WORKERS=0) ninguém consumiria o que a rotina recolocasse
        tarefas.append(asyncio.create_task(reprocessar_erros_loop(fila_global)))
    return tarefas
