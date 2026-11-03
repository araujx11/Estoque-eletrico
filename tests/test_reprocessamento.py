"""
Caso 7 (reprocessamento) e comportamento após reinício da API.

ACHADO 1 (corrigido na Sprint 05): processar a mesma requisição de novo, em sequência ou ao mesmo tempo
em dois workers, não debita o estoque outra vez. Estes testes eram xfail(strict=True) e agora passam.
"""
import asyncio

from sqlalchemy import select, func

from app.models import MovimentacaoEstoque, Requisicao, StatusRequisicao as S
from app.services.fila_service import fila_global, reenfileirar_pendentes
from tests.helpers import criar_material, criar_obra, criar_requisicoes, estoque_de, processar, rodar_concorrente


async def test_reprocessar_a_mesma_requisicao_nao_debita_duas_vezes(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])

    primeira = await processar(SessionTeste, rid)
    assert primeira.status == S.APROVADA and await estoque_de(SessionTeste, mid) == 70
    await processar(SessionTeste, rid)            # segunda chamada para a MESMA requisição

    assert await estoque_de(SessionTeste, mid) == 70, "o estoque foi debitado duas vezes"


async def test_mesma_requisicao_em_dois_workers_simultaneos(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])

    await rodar_concorrente([processar(SessionTeste, rid, "w1"), processar(SessionTeste, rid, "w2")])

    assert await estoque_de(SessionTeste, mid) == 70, "o estoque foi debitado duas vezes"


async def test_reinicio_reenfileira_pendentes_e_em_processamento(SessionTeste):
    """A fila é em memória, mas reenfileirar_pendentes() (chamada no lifespan) recupera o que ficou aberto."""
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [1, 2, 3])
    await processar(SessionTeste, ids[0])         # esta já foi resolvida: não pode voltar para a fila

    assert fila_global.tamanho() == 0             # simula o processo recém-iniciado: fila vazia
    await reenfileirar_pendentes()

    assert fila_global.tamanho() == 2
    voltaram = {await asyncio.wait_for(fila_global.proximo(), 2) for _ in range(2)}
    assert voltaram == set(ids[1:])


async def _saidas_de(Sessao, rid: str) -> int:
    async with Sessao() as db:
        return (await db.execute(select(func.count()).select_from(MovimentacaoEstoque).where(MovimentacaoEstoque.requisicao_id == rid))).scalar_one()


async def test_reprocessar_varias_vezes_mantem_resultado_e_uma_unica_saida(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])

    primeira = await processar(SessionTeste, rid, "w1")
    for _ in range(3):
        repetida = await processar(SessionTeste, rid, "w2")

    assert repetida.status == S.APROVADA and repetida.quantidade_atendida == 30
    assert repetida.worker_id == "w1", "o reprocessamento não pode sobrescrever quem resolveu a requisição"
    assert repetida.processado_em == primeira.processado_em
    assert await estoque_de(SessionTeste, mid) == 70
    assert await _saidas_de(SessionTeste, rid) == 1, "só pode haver UMA saída no livro-razão"


async def test_requisicao_parcial_ou_negada_tambem_e_idempotente(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=10)
    oid = await criar_obra(SessionTeste)
    parcial, negada = await criar_requisicoes(SessionTeste, oid, mid, [25, 5])

    assert (await processar(SessionTeste, parcial)).status == S.PARCIAL       # leva os 10 que existiam
    assert (await processar(SessionTeste, negada)).status == S.NEGADA         # estoque zerado
    await processar(SessionTeste, parcial)
    await processar(SessionTeste, negada)

    assert await estoque_de(SessionTeste, mid) == 0
    assert await _saidas_de(SessionTeste, parcial) == 1 and await _saidas_de(SessionTeste, negada) == 0


async def test_muitos_workers_na_mesma_requisicao_debitam_uma_vez(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])

    await rodar_concorrente([processar(SessionTeste, rid, f"w{i}") for i in range(10)])

    assert await estoque_de(SessionTeste, mid) == 70
    assert await _saidas_de(SessionTeste, rid) == 1


async def test_requisicao_em_erro_ainda_pode_ser_processada(SessionTeste):
    """ERRO não é estado final: a transação que falhou foi desfeita, nada foi debitado."""
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])
    async with SessionTeste() as db:
        (await db.get(Requisicao, rid)).status = S.ERRO
        await db.commit()

    resultado = await processar(SessionTeste, rid)

    assert resultado.status == S.APROVADA and await estoque_de(SessionTeste, mid) == 70


async def test_gancho_ao_travar_o_material_e_chamado_uma_vez_em_cada_caminho(SessionTeste):
    """O gancho (usado pela fila para liberar a vez do próximo) é chamado ao travar o material e também quando a requisição já estava resolvida."""
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])
    chamadas = []

    from app.services.estoque_service import processar_requisicao
    async with SessionTeste() as db:
        await processar_requisicao(db, rid, "w1", ao_travar_material=lambda: chamadas.append("primeira"))
    async with SessionTeste() as db:
        await processar_requisicao(db, rid, "w2", ao_travar_material=lambda: chamadas.append("repetida"))

    assert chamadas == ["primeira", "repetida"]
    assert await estoque_de(SessionTeste, mid) == 70
