"""
CÓPIA CONGELADA do processamento ANTES do registro de movimentações (uso exclusivo do benchmark).

Serve só para comparar, em execuções intercaladas, o desempenho da versão anterior com o da atual.
Nunca é importada por app/.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Material, Requisicao, StatusRequisicao


async def processar_requisicao_v1(db: AsyncSession, requisicao_id: str, worker_id: str) -> Requisicao:
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

        requisicao.processado_em = datetime.now(timezone.utc).replace(tzinfo=None)
        # commit acontece ao sair do `async with db.begin()`, liberando o lock

    await db.refresh(requisicao)
    return requisicao


# Índices acrescentados em `requisicoes` junto com o livro-razão. A "versão anterior" roda sem eles.
INDICES_NOVOS = {
    "ix_requisicoes_obra_id": "requisicoes (obra_id)",
    "ix_requisicoes_material_id": "requisicoes (material_id)",
    "ix_requisicoes_status": "requisicoes (status)",
    "ix_requisicoes_criado_em": "requisicoes (criado_em)",
}
