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
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Material, Requisicao, StatusRequisicao


async def processar_requisicao(db: AsyncSession, requisicao_id: str, worker_id: str) -> Requisicao:
    """
    Processa uma única requisição de forma atômica e segura contra concorrência.

    Passos:
      1. Trava a linha do material (FOR UPDATE) -> ponto de exclusão mútua.
      2. Verifica quanto está disponível.
      3. Decide: aprovada / parcial / negada.
      4. Debita do estoque e grava o resultado.
      5. Commit libera o lock para o próximo worker.
    """
    async with db.begin():
        requisicao = await db.get(Requisicao, requisicao_id)
        if requisicao is None:
            raise ValueError(f"Requisição {requisicao_id} não encontrada")

        requisicao.status = StatusRequisicao.EM_PROCESSAMENTO
        requisicao.worker_id = worker_id

        # --- ponto crítico: trava a linha do material até o commit ---
        stmt = select(Material).where(Material.id == requisicao.material_id).with_for_update()
        result = await db.execute(stmt)
        material = result.scalar_one()

        disponivel = material.quantidade_estoque
        solicitado = requisicao.quantidade_solicitada

        if disponivel <= 0:
            requisicao.status = StatusRequisicao.NEGADA
            requisicao.quantidade_atendida = 0
        elif disponivel >= solicitado:
            material.quantidade_estoque -= solicitado
            requisicao.quantidade_atendida = solicitado
            requisicao.status = StatusRequisicao.APROVADA
        else:
            # déficit: entrega o que sobrou e marca como parcial
            material.quantidade_estoque = 0
            requisicao.quantidade_atendida = disponivel
            requisicao.status = StatusRequisicao.PARCIAL

        requisicao.processado_em = datetime.utcnow()
        # commit acontece ao sair do `async with db.begin()`, liberando o lock

    await db.refresh(requisicao)
    return requisicao
