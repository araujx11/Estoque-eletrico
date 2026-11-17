"""
Motor de alocação de estoque.

Este é o ponto crítico do sistema: é aqui que garantimos que, mesmo com
vários workers processando requisições ao mesmo tempo, o estoque nunca
fica negativo e nenhuma quantidade é "contada duas vezes".

Estratégia: lock pessimista via `SELECT ... FOR UPDATE`.
Quando o worker abre a transação e faz esse SELECT na linha do material,
o PostgreSQL trava essa linha. Qualquer outro worker que tente fazer
SELECT FOR UPDATE na MESMA linha precisa esperar até o primeiro dar
COMMIT ou ROLLBACK. Isso serializa o acesso ao mesmo material sem
serializar o sistema inteiro (materiais diferentes continuam sendo
processados em paralelo, sem se bloquear).

Idempotência (achado 1): a linha da REQUISIÇÃO também é travada, e quem já foi resolvida
(aprovada, parcial ou negada) é devolvida como está, sem debitar de novo. Ordem dos locks:
sempre requisição -> material, para todos os workers (sem risco de deadlock).
"""
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ZERO, Material, MovimentacaoEstoque, Requisicao, StatusRequisicao, TipoMovimentacao

logger = logging.getLogger("estoque_service")

# estados finais: o débito (se houve) já foi feito e não pode se repetir. ERRO não entra: a transação
# que falhou foi desfeita, então nada foi debitado e a requisição ainda pode ser processada.
RESOLVIDAS = (StatusRequisicao.APROVADA, StatusRequisicao.PARCIAL, StatusRequisicao.NEGADA)


async def processar_requisicao(
    db: AsyncSession, requisicao_id: str, worker_id: str, ao_travar_material: Callable[[], None] | None = None
) -> Requisicao:
    """
    Processa uma única requisição de forma atômica, segura contra concorrência e idempotente.

    Passos:
      1. Trava a linha da requisição (FOR UPDATE); se já foi resolvida, devolve sem fazer nada.
      2. Trava a linha do material (FOR UPDATE) -> ponto de exclusão mútua do estoque.
      3. Verifica quanto está disponível.
      4. Decide: aprovada / parcial / negada.
      5. Debita do estoque e grava o resultado.
      6. Commit libera os locks para o próximo worker.

    `ao_travar_material` (opcional) é chamado assim que a requisição passa do ponto de disputa pela ordem: logo depois de
    travar o material, ou quando descobre que a requisição já estava resolvida. A fila usa isso para liberar a vez do próximo
    item do mesmo material sem esperar o commit (o próximo só pede o lock depois deste, então a ordem continua estrita).
    """
    async with db.begin():
        requisicao = (await db.execute(
            select(Requisicao).where(Requisicao.id == requisicao_id).with_for_update()
        )).scalar_one_or_none()
        if requisicao is None:
            raise ValueError(f"Requisição {requisicao_id} não encontrada")

        if requisicao.status in RESOLVIDAS:
            logger.info("[%s] requisicao=%s já estava %s: ignorada (sem novo débito)",
                        worker_id, requisicao_id, requisicao.status.value)
            if ao_travar_material:
                ao_travar_material()
        else:
            await _alocar(db, requisicao, worker_id, ao_travar_material)
        # commit acontece ao sair do `async with db.begin()`, liberando os locks

    await db.refresh(requisicao)
    return requisicao


async def _alocar(db: AsyncSession, requisicao: Requisicao, worker_id: str, ao_travar_material: Callable[[], None] | None) -> None:
    requisicao.status = StatusRequisicao.EM_PROCESSAMENTO
    requisicao.worker_id = worker_id

    # --- ponto crítico: trava a linha do material até o commit ---
    stmt = select(Material).where(Material.id == requisicao.material_id).with_for_update()
    result = await db.execute(stmt)
    material = result.scalar_one()
    if ao_travar_material:
        ao_travar_material()        # já temos o lock: quem vem atrás só vai pedi-lo depois de nós

    disponivel = material.quantidade_estoque
    solicitado = requisicao.quantidade_solicitada

    if disponivel <= 0:
        requisicao.status = StatusRequisicao.NEGADA
        requisicao.quantidade_atendida = ZERO
    elif disponivel >= solicitado:
        material.quantidade_estoque -= solicitado
        requisicao.quantidade_atendida = solicitado
        requisicao.status = StatusRequisicao.APROVADA
    else:
        # déficit: entrega o que sobrou e marca como parcial
        material.quantidade_estoque = ZERO
        requisicao.quantidade_atendida = disponivel
        requisicao.status = StatusRequisicao.PARCIAL

    if requisicao.quantidade_atendida > 0:
        # livro-razão: a SAÍDA entra na MESMA transação do débito (segura o mesmo lock do material),
        # então saldo_apos reflete exatamente o estoque logo depois deste débito
        db.add(MovimentacaoEstoque(
            material_id=material.id,
            tipo=TipoMovimentacao.SAIDA,
            quantidade=requisicao.quantidade_atendida,
            saldo_apos=material.quantidade_estoque,
            requisicao_id=requisicao.id,
        ))

    requisicao.processado_em = datetime.now(timezone.utc).replace(tzinfo=None)
