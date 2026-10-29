"""
CONTROLE NEGATIVO — uso exclusivo do benchmark (nunca importado por app/).

Mesma regra de alocação (e mesmo registro de movimentação) de app/services/estoque_service.py, porém SEM `SELECT ... FOR UPDATE`:
lê o estoque, decide e depois escreve. Sob concorrência dois workers leem o mesmo saldo e ambos
gravam "saldo - pedido" (lost update): o sistema entrega mais material do que existe.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import Material, MovimentacaoEstoque, Requisicao, StatusRequisicao, TipoMovimentacao


async def processar_requisicao_sem_lock(db, requisicao_id: str, worker_id: str) -> Requisicao:
    async with db.begin():
        requisicao = await db.get(Requisicao, requisicao_id)
        if requisicao is None:
            raise ValueError(f"Requisição {requisicao_id} não encontrada")

        requisicao.status = StatusRequisicao.EM_PROCESSAMENTO
        requisicao.worker_id = worker_id

        # <<< única diferença: sem .with_for_update() — nenhuma linha é travada >>>
        material = (await db.execute(
            select(Material).where(Material.id == requisicao.material_id)
        )).scalar_one()

        disponivel = material.quantidade_estoque
        solicitado = requisicao.quantidade_solicitada

        if disponivel <= 0:
            requisicao.status = StatusRequisicao.NEGADA
            requisicao.quantidade_atendida = 0
        elif disponivel >= solicitado:
            material.quantidade_estoque -= solicitado      # UPDATE com o valor calculado de uma leitura já velha
            requisicao.quantidade_atendida = solicitado
            requisicao.status = StatusRequisicao.APROVADA
        else:
            material.quantidade_estoque = 0
            requisicao.quantidade_atendida = disponivel
            requisicao.status = StatusRequisicao.PARCIAL

        if requisicao.quantidade_atendida > 0:    # espelha a produção: a saída também entra no livro-razão
            db.add(MovimentacaoEstoque(
                material_id=material.id, tipo=TipoMovimentacao.SAIDA, quantidade=requisicao.quantidade_atendida,
                saldo_apos=material.quantidade_estoque, requisicao_id=requisicao.id))

        requisicao.processado_em = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.refresh(requisicao)
    return requisicao
