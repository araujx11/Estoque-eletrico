"""Movimentações de estoque: livro-razão, entradas, ajustes e conservação (inclusive sob concorrência)."""
import random

import pytest
from sqlalchemy import func, select

from app.models import MovimentacaoEstoque, StatusRequisicao as S, TipoMovimentacao as T
from tests.helpers import (
    criar_material, criar_obra, criar_requisicoes, estoque_de, movimentacoes_de, processar, requisicoes_de,
    rodar_concorrente,
)


def _cadeia_coerente(movs, saldo_inicial):
    """Em ordem de id, cada saldo_apos == saldo anterior ± quantidade."""
    saldo = saldo_inicial
    for m in movs:
        saldo = saldo - m.quantidade if m.tipo == T.SAIDA else saldo + m.quantidade
        assert abs(m.saldo_apos - saldo) < 1e-6, f"movimentação {m.id} ({m.tipo.value}): saldo_apos {m.saldo_apos} != {saldo}"
    return saldo


# ---------------------------------------------------------------- SAÍDA registrada pelo processamento

async def test_saida_registrada_com_saldo_coerente(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [30, 50, 40, 5])   # 30 ok, 50 ok, 40 parcial (20), 5 negada
    for rid in ids:
        await processar(SessionTeste, rid)

    movs = await movimentacoes_de(SessionTeste, mid)
    assert [(m.tipo, m.quantidade, m.saldo_apos) for m in movs] == [(T.SAIDA, 30, 70), (T.SAIDA, 50, 20), (T.SAIDA, 20, 0)]
    assert [m.requisicao_id for m in movs] == ids[:3]          # a negada não movimenta estoque
    assert all(m.usuario_id is None for m in movs)             # saída é do sistema, não de uma pessoa
    assert _cadeia_coerente(movs, 100) == await estoque_de(SessionTeste, mid) == 0


@pytest.mark.stress
async def test_conservacao_no_estresse_de_200_requisicoes(SessionTeste):
    rng = random.Random(42)
    inicial = 1000
    mid = await criar_material(SessionTeste, estoque=inicial)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [rng.randint(1, 30) for _ in range(200)])

    await rodar_concorrente([processar(SessionTeste, rid, f"w{i % 8}") for i, rid in enumerate(ids)])

    movs = await movimentacoes_de(SessionTeste, mid)
    reqs = await requisicoes_de(SessionTeste, mid)
    saidas = sum(m.quantidade for m in movs if m.tipo == T.SAIDA)
    assert saidas == sum(r.quantidade_atendida for r in reqs)                  # ledger == requisições
    assert inicial - saidas == await estoque_de(SessionTeste, mid)             # conservação
    assert len(movs) == sum(1 for r in reqs if r.quantidade_atendida > 0)      # 1 saída por pedido que movimentou
    assert _cadeia_coerente(movs, inicial) == await estoque_de(SessionTeste, mid)   # saldo_apos coerente com a ordem do lock


@pytest.mark.stress
async def test_conservacao_com_entradas_e_saidas_concorrentes(client, contas, SessionTeste):
    gestor = contas["gestor"]
    rng = random.Random(5)
    material = (await client.post("/materiais", headers=gestor, json={
        "nome": "Disjuntor", "unidade": "un", "quantidade_estoque": 300, "estoque_critico": 10})).json()
    mid = material["id"]
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [rng.randint(1, 10) for _ in range(120)])

    async def entrada():
        r = await client.post("/movimentacoes/entrada", headers=gestor, json={"material_id": mid, "quantidade": 20})
        assert r.status_code == 201, r.text

    # 120 débitos e 15 entradas de 20 un disputando a mesma linha do material
    await rodar_concorrente([processar(SessionTeste, rid) for rid in ids] + [entrada() for _ in range(15)], limite=15)

    movs = await movimentacoes_de(SessionTeste, mid)
    final = await estoque_de(SessionTeste, mid)
    entradas = sum(m.quantidade for m in movs if m.tipo == T.ENTRADA)
    saidas = sum(m.quantidade for m in movs if m.tipo == T.SAIDA)
    assert entradas == 300 + 15 * 20                                  # saldo inicial + 15 entradas, nenhuma perdida
    assert entradas - saidas == final and final >= 0                  # inicial + entradas - saídas == atual
    assert _cadeia_coerente(movs, 0) == final


# ---------------------------------------------------------------- criação / edição de material

async def test_material_criado_pela_api_gera_entrada_do_saldo_inicial(contas, SessionTeste):
    movs = await movimentacoes_de(SessionTeste, contas["material"]["id"])
    assert len(movs) == 1
    m = movs[0]
    assert (m.tipo, m.quantidade, m.saldo_apos, m.observacao) == (T.ENTRADA, 100, 100, "Estoque inicial")
    assert m.usuario_id is not None


async def test_edicao_de_quantidade_vira_ajuste(client, contas, SessionTeste):
    mid = contas["material"]["id"]
    r = await client.put(f"/materiais/{mid}", headers=contas["almox"], json={
        "nome": "Cabo 10mm2", "unidade": "m", "quantidade_estoque": 80, "estoque_critico": 10})
    assert r.status_code == 200
    movs = await movimentacoes_de(SessionTeste, mid)
    assert [(m.tipo, m.quantidade, m.saldo_apos) for m in movs] == [(T.ENTRADA, 100, 100), (T.AJUSTE, -20, 80)]
    # editar sem mexer na quantidade não cria movimentação
    await client.put(f"/materiais/{mid}", headers=contas["almox"], json={
        "nome": "Cabo 10mm2", "unidade": "m", "quantidade_estoque": 80, "estoque_critico": 15})
    assert len(await movimentacoes_de(SessionTeste, mid)) == 2


# ---------------------------------------------------------------- entrada

async def test_entrada_soma_estoque_e_registra(client, contas, SessionTeste):
    mid = contas["material"]["id"]
    r = await client.post("/movimentacoes/entrada", headers=contas["almox"],
                          json={"material_id": mid, "quantidade": 25.5, "observacao": "NF 1234"})
    assert r.status_code == 201 and r.json()["quantidade_estoque"] == 125.5
    ultima = (await movimentacoes_de(SessionTeste, mid))[-1]
    assert (ultima.tipo, ultima.quantidade, ultima.saldo_apos, ultima.observacao) == (T.ENTRADA, 25.5, 125.5, "NF 1234")
    assert ultima.usuario_id is not None


@pytest.mark.parametrize("corpo,codigo", [
    ({"quantidade": 0}, 422), ({"quantidade": -3}, 422), ({"quantidade": "x"}, 422), ({"material_id": 99999, "quantidade": 5}, 404),
])
async def test_entrada_invalida(client, contas, corpo, codigo):
    payload = {"material_id": contas["material"]["id"], **corpo}
    assert (await client.post("/movimentacoes/entrada", headers=contas["gestor"], json=payload)).status_code == codigo


# ---------------------------------------------------------------- ajuste

async def test_ajuste_positivo_e_negativo(client, contas, SessionTeste):
    mid, h = contas["material"]["id"], contas["almox"]
    r = await client.post("/movimentacoes/ajuste", headers=h, json={"material_id": mid, "quantidade": -30, "observacao": "perda no transporte"})
    assert r.status_code == 201 and r.json()["quantidade_estoque"] == 70
    r = await client.post("/movimentacoes/ajuste", headers=h, json={"material_id": mid, "quantidade": 5, "observacao": "achado no inventário"})
    assert r.json()["quantidade_estoque"] == 75
    movs = await movimentacoes_de(SessionTeste, mid)
    assert [(m.tipo, m.quantidade, m.saldo_apos) for m in movs[1:]] == [(T.AJUSTE, -30, 70), (T.AJUSTE, 5, 75)]
    assert movs[1].observacao == "perda no transporte"


async def test_ajuste_nao_deixa_estoque_negativo(client, contas, SessionTeste):
    mid = contas["material"]["id"]
    r = await client.post("/movimentacoes/ajuste", headers=contas["gestor"],
                          json={"material_id": mid, "quantidade": -100.5, "observacao": "contagem errada"})
    assert r.status_code == 409 and "negativo" in r.json()["detail"]
    assert await estoque_de(SessionTeste, mid) == 100                      # nada mudou
    assert len(await movimentacoes_de(SessionTeste, mid)) == 1             # e nada foi registrado
    # zerar exatamente é permitido
    ok = await client.post("/movimentacoes/ajuste", headers=contas["gestor"],
                           json={"material_id": mid, "quantidade": -100, "observacao": "baixa total"})
    assert ok.status_code == 201 and ok.json()["quantidade_estoque"] == 0


@pytest.mark.parametrize("corpo", [
    {"quantidade": 5},                                   # sem motivo
    {"quantidade": 5, "observacao": ""},
    {"quantidade": 5, "observacao": "  a "},             # motivo curto demais
    {"quantidade": 0, "observacao": "nada a ajustar"},   # ajuste zero
])
async def test_ajuste_exige_motivo_e_quantidade(client, contas, corpo):
    payload = {"material_id": contas["material"]["id"], **corpo}
    assert (await client.post("/movimentacoes/ajuste", headers=contas["gestor"], json=payload)).status_code == 422


async def test_engenheiro_nao_registra_nem_ve_movimentacoes(client, contas):
    mid, h = contas["material"]["id"], contas["eng"]
    assert (await client.post("/movimentacoes/entrada", headers=h, json={"material_id": mid, "quantidade": 5})).status_code == 403
    assert (await client.post("/movimentacoes/ajuste", headers=h, json={"material_id": mid, "quantidade": 5, "observacao": "teste"})).status_code == 403
    assert (await client.get("/movimentacoes", headers=h)).status_code == 403


# ---------------------------------------------------------------- listagem, filtros, paginação

async def test_listagem_com_filtros_e_paginacao(client, contas):
    mid, h = contas["material"]["id"], contas["almox"]
    for _ in range(3):
        await client.post("/movimentacoes/entrada", headers=h, json={"material_id": mid, "quantidade": 1, "observacao": "lote"})
    for _ in range(2):
        await client.post("/movimentacoes/ajuste", headers=h, json={"material_id": mid, "quantidade": -1, "observacao": "quebra"})

    tudo = (await client.get("/movimentacoes", headers=h)).json()
    assert tudo["total"] == 6                                           # 1 saldo inicial + 3 entradas + 2 ajustes
    assert [m["tipo"] for m in tudo["itens"]][:2] == ["ajuste", "ajuste"]   # mais recentes primeiro

    so_ajustes = (await client.get("/movimentacoes?tipo=ajuste", headers=h)).json()
    assert so_ajustes["total"] == 2 and all(m["tipo"] == "ajuste" for m in so_ajustes["itens"])
    assert (await client.get("/movimentacoes?busca=quebra", headers=h)).json()["total"] == 2
    assert (await client.get(f"/movimentacoes?material_id={mid + 99}", headers=h)).json()["total"] == 0
    assert (await client.get("/movimentacoes?de=2999-01-01", headers=h)).json()["total"] == 0
    assert (await client.get("/movimentacoes?ate=2000-01-01", headers=h)).json()["total"] == 0

    p1 = (await client.get("/movimentacoes?tamanho=4&pagina=1", headers=h)).json()
    p2 = (await client.get("/movimentacoes?tamanho=4&pagina=2", headers=h)).json()
    assert (len(p1["itens"]), len(p2["itens"]), p1["total"]) == (4, 2, 6)
    assert not {m["id"] for m in p1["itens"]} & {m["id"] for m in p2["itens"]}
    item = tudo["itens"][0]
    assert item["material_nome"] == "Cabo 10mm2" and item["unidade"] == "m" and item["usuario_nome"] == "Alma"


# ---------------------------------------------------------------- remoção mantém o livro-razão coerente

async def test_remover_material_apaga_suas_movimentacoes(client, contas, SessionTeste):
    mid = contas["material"]["id"]
    assert (await client.delete(f"/materiais/{mid}", headers=contas["gestor"])).status_code == 204
    async with SessionTeste() as db:
        assert (await db.execute(select(func.count()).select_from(MovimentacaoEstoque)
                                 .where(MovimentacaoEstoque.material_id == mid))).scalar_one() == 0


async def test_remover_obra_com_historico_preserva_as_saidas(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, estoque=50)
    oid = await criar_obra(SessionTeste, "Obra descartável")
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [10])
    assert (await processar(SessionTeste, rid)).status == S.APROVADA

    assert (await client.delete(f"/obras/{oid}", headers=contas["gestor"])).status_code == 409   # pede confirmação
    assert (await client.delete(f"/obras/{oid}?forcar=true", headers=contas["gestor"])).status_code == 204
    movs = await movimentacoes_de(SessionTeste, mid)
    assert len(movs) == 1 and movs[0].tipo == T.SAIDA and movs[0].requisicao_id is None   # saída fica, sem o vínculo
