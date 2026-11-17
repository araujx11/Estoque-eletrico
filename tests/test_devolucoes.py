"""RF15 / RN10: devoluções de material."""
import asyncio
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.models import LogAuditoria, TipoMovimentacao as T
from tests.helpers import (
    criar_material, criar_obra, criar_requisicoes, estoque_de, movimentacoes_de, processar, requisicoes_de,
)


async def _atender(Sessao, estoque, pedido):
    """Material com `estoque`, uma obra e uma requisição de `pedido` já processada. Devolve (material, obra, requisição)."""
    mid, oid = await criar_material(Sessao, estoque=estoque), await criar_obra(Sessao)
    (rid,) = await criar_requisicoes(Sessao, oid, mid, [pedido])
    await processar(Sessao, rid)
    return mid, oid, rid


def _dev(mid, qtd, **extra):
    return {"material_id": mid, "quantidade": qtd, "observacao": "sobra da obra", **extra}


async def test_devolucao_valida_soma_ao_estoque_e_grava_o_livro_razao(client, gestor, SessionTeste):
    mid, oid, rid = await _atender(SessionTeste, 100, 40)                       # estoque 60, obra recebeu 40
    r = await client.post("/movimentacoes/devolucao", json=_dev(mid, 15.5, obra_id=oid, requisicao_id=rid), headers=gestor)
    assert r.status_code == 201, r.text
    assert r.json()["quantidade_estoque"] == 75.5
    *_, dev = await movimentacoes_de(SessionTeste, mid)
    assert dev.tipo == T.DEVOLUCAO and dev.quantidade == D("15.5") and dev.saldo_apos == D("75.5")
    assert dev.obra_id == oid and dev.requisicao_id == rid and dev.usuario_id is not None and dev.observacao == "sobra da obra"
    async with SessionTeste() as db:
        assert (await db.execute(select(func.count()).select_from(LogAuditoria)
                                 .where(LogAuditoria.acao == "devolucao_estoque"))).scalar_one() == 1


async def test_historico_mostra_tipo_devolucao_e_obra(client, gestor, SessionTeste):
    mid, oid, _ = await _atender(SessionTeste, 100, 40)
    await client.post("/movimentacoes/devolucao", json=_dev(mid, 5, obra_id=oid), headers=gestor)
    itens = (await client.get("/movimentacoes?tipo=devolucao", headers=gestor)).json()["itens"]
    assert len(itens) == 1 and itens[0]["tipo"] == "devolucao" and itens[0]["obra_nome"] == "Obra"


async def test_acima_do_atendido_e_recusada_com_409_em_portugues(client, gestor, SessionTeste):
    mid, oid, _ = await _atender(SessionTeste, 100, 40)
    r = await client.post("/movimentacoes/devolucao", json=_dev(mid, 40.001, obra_id=oid), headers=gestor)
    assert r.status_code == 409 and "acima do limite" in r.json()["detail"] and "40" in r.json()["detail"]
    assert await estoque_de(SessionTeste, mid) == 60                              # nada mudou


async def test_limite_considera_devolucoes_ja_feitas(client, gestor, SessionTeste):
    mid, oid, _ = await _atender(SessionTeste, 100, 40)
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 30, obra_id=oid), headers=gestor)).status_code == 201
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 10.001, obra_id=oid), headers=gestor)).status_code == 409
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 10, obra_id=oid), headers=gestor)).status_code == 201
    assert await estoque_de(SessionTeste, mid) == 100


async def test_limite_e_por_obra(client, gestor, SessionTeste):
    mid, oid, _ = await _atender(SessionTeste, 100, 40)
    outra = await criar_obra(SessionTeste, nome="Outra")                          # esta obra nunca recebeu nada
    r = await client.post("/movimentacoes/devolucao", json=_dev(mid, 1, obra_id=outra), headers=gestor)
    assert r.status_code == 409 and "esta obra" in r.json()["detail"]


async def test_parcial_so_devolve_o_que_foi_atendido(client, gestor, SessionTeste):
    mid, oid, _ = await _atender(SessionTeste, 30, 50)                            # pediu 50, levou 30
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 31, obra_id=oid), headers=gestor)).status_code == 409
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 30, obra_id=oid), headers=gestor)).status_code == 201


async def test_material_obra_ou_requisicao_inexistente(client, gestor, SessionTeste):
    mid, oid, rid = await _atender(SessionTeste, 100, 40)
    assert (await client.post("/movimentacoes/devolucao", json=_dev(99999, 1), headers=gestor)).status_code == 404
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 1, obra_id=99999), headers=gestor)).status_code == 404
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 1, requisicao_id="nao-existe"), headers=gestor)).status_code == 404
    outro = await criar_material(SessionTeste, nome="Outro", estoque=10)
    assert (await client.post("/movimentacoes/devolucao", json=_dev(outro, 1, requisicao_id=rid), headers=gestor)).status_code == 422


@pytest.mark.parametrize("corpo", [
    {"quantidade": 0}, {"quantidade": -1}, {"quantidade": 1.2345}, {"observacao": "ab"}, {"observacao": "   "},
])
async def test_validacao_da_entrada(client, gestor, dados, corpo):
    r = await client.post("/movimentacoes/devolucao", json={**_dev(dados["material"]["id"], 1), **corpo}, headers=gestor)
    assert r.status_code == 422


async def test_permissoes_almoxarife_pode_engenheiro_nao(client, almox, eng, gestor, SessionTeste):
    mid, oid, _ = await _atender(SessionTeste, 100, 40)
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 1, obra_id=oid), headers=eng)).status_code == 403
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 1, obra_id=oid), headers=almox)).status_code == 201
    assert (await client.post("/movimentacoes/devolucao", json=_dev(mid, 1, obra_id=oid))).status_code == 401


async def test_relatorio_de_consumo_desconta_devolucoes(client, gestor, SessionTeste):
    mid, oid, _ = await _atender(SessionTeste, 100, 40)
    await client.post("/movimentacoes/devolucao", json=_dev(mid, 12.5, obra_id=oid), headers=gestor)
    for agrupar in ("obra_material", "material"):
        itens = (await client.get(f"/relatorios/consumo?agrupar={agrupar}", headers=gestor)).json()["itens"]
        assert len(itens) == 1 and itens[0]["quantidade_atendida"] == 27.5, agrupar
        assert itens[0]["quantidade_solicitada"] == 40                             # o pedido em si não muda


async def test_devolucoes_e_pedidos_ao_mesmo_tempo_saldo_nunca_negativo_e_razao_consistente(client, gestor, SessionTeste):
    """Várias devoluções simultâneas (cada uma tenta devolver 10 de um limite de 40) e pedidos que consomem o estoque
    ao mesmo tempo: nenhuma devolução passa do que foi atendido, o saldo nunca fica negativo e inicial + devoluções − saídas == estoque."""
    inicial = D("50")
    mid, oid, _ = await _atender(SessionTeste, inicial, 40)                         # estoque 10, devolvível 40
    pedidos = await criar_requisicoes(SessionTeste, oid, mid, [D("7")] * 10)         # demanda 70 > 10 + devoluções

    async def devolver():
        return (await client.post("/movimentacoes/devolucao", json=_dev(mid, 10, obra_id=oid), headers=gestor)).status_code

    resultados = await asyncio.gather(*([devolver() for _ in range(8)] + [processar(SessionTeste, rid, f"w{i}") for i, rid in enumerate(pedidos)]))
    codigos = resultados[:8]
    assert codigos.count(201) >= 4 and set(codigos) <= {201, 409}                  # o limite sobe conforme os pedidos são atendidos

    final = await estoque_de(SessionTeste, mid)
    movs = await movimentacoes_de(SessionTeste, mid)
    saldo = inicial                                                                   # o helper cria o material sem a entrada inicial
    for m in movs:                                                                   # ordem do lock = ordem dos ids
        saldo = saldo - m.quantidade if m.tipo == T.SAIDA else saldo + m.quantidade
        assert m.saldo_apos == saldo and m.saldo_apos >= 0
    assert saldo == final >= 0
    devolvido = sum((m.quantidade for m in movs if m.tipo == T.DEVOLUCAO), D(0))
    saidas = sum((m.quantidade for m in movs if m.tipo == T.SAIDA), D(0))
    atendido = sum((r.quantidade_atendida for r in await requisicoes_de(SessionTeste, mid)), D(0))
    assert devolvido == 10 * codigos.count(201)
    assert devolvido <= atendido, "devolveu mais do que foi atendido"                # a regra vale mesmo com tudo ao mesmo tempo
    assert saidas == atendido and inicial + devolvido - saidas == final
