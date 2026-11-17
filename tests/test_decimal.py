"""Achado 8: quantidades em NUMERIC(14,3)/Decimal. Somas exatas, conservação com frações e JSON ainda numérico."""
import random
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.models import Material, MovimentacaoEstoque, StatusRequisicao as S, TipoMovimentacao as T
from tests.helpers import (
    criar_material, criar_obra, criar_requisicoes, estoque_de, movimentacoes_de, processar, requisicoes_de,
    rodar_concorrente,
)


# ---------------------------------------------------------------- aritmética exata

async def test_somar_0_1_dez_mil_vezes_da_exatamente_1000(SessionTeste):
    """Com float, 0.1 somado 10.000 vezes dá 1000.0000000001588. No banco NUMERIC e em Decimal é exato."""
    acumulado = 0.0                              # (sum() do Python 3.12+ compensa o erro; o débito/crédito do estoque não)
    for _ in range(10_000):
        acumulado += 0.1
    assert acumulado != 1000                     # a armadilha que motivou a troca de tipo
    mid = await criar_material(SessionTeste, estoque=D(0))

    async with SessionTeste() as db:            # (a) no Python, em cima do modelo
        m = await db.get(Material, mid)
        for _ in range(10_000):
            m.quantidade_estoque += D("0.1")
        await db.commit()
    assert await estoque_de(SessionTeste, mid) == D("1000.000")

    async with SessionTeste() as db:            # (b) no banco: SUM de 10.000 linhas de 0,1
        db.add_all([MovimentacaoEstoque(material_id=mid, tipo=T.ENTRADA, quantidade=D("0.1"), saldo_apos=D("0.1"))
                    for _ in range(10_000)])
        await db.commit()
        total = (await db.execute(select(func.sum(MovimentacaoEstoque.quantidade)))).scalar_one()
    assert total == D("1000.000")


async def test_cenario_100_40_30_50_continua_igual(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [40, 30, 50])
    resultados = [await processar(SessionTeste, rid) for rid in ids]
    assert [r.status for r in resultados] == [S.APROVADA, S.APROVADA, S.PARCIAL]
    assert [r.quantidade_atendida for r in resultados] == [40, 30, 30]
    assert await estoque_de(SessionTeste, mid) == 0


# ---------------------------------------------------------------- conservação com frações

async def test_conservacao_com_quantidades_decimais(SessionTeste):
    inicial = D("20.000")
    mid = await criar_material(SessionTeste, estoque=inicial)
    oid = await criar_obra(SessionTeste)
    pedidos = [D("0.1"), D("0.25"), D("12.345"), D("0.1"), D("5"), D("3.7")]      # soma 21.495 > 20: o último é parcial
    ids = await criar_requisicoes(SessionTeste, oid, mid, pedidos)
    for rid in ids:
        await processar(SessionTeste, rid)

    reqs = await requisicoes_de(SessionTeste, mid)
    atendido = sum((r.quantidade_atendida for r in reqs), D(0))
    final = await estoque_de(SessionTeste, mid)
    assert atendido + final == inicial                                              # IGUALDADE exata, sem tolerância
    assert atendido == D("20.000") and final == 0
    assert [r.quantidade_atendida for r in sorted(reqs, key=lambda r: r.criado_em or 0)].count(D("12.345")) == 1

    saldo = inicial
    for m in await movimentacoes_de(SessionTeste, mid):
        saldo -= m.quantidade
        assert m.saldo_apos == saldo                                                # livro-razão exato


@pytest.mark.stress
async def test_estresse_200_requisicoes_fracionadas(SessionTeste):
    rnd = random.Random(8)
    inicial = D("300.000")
    mid = await criar_material(SessionTeste, estoque=inicial)
    oid = await criar_obra(SessionTeste)
    pedidos = [D(rnd.randint(1, 4000)) / 1000 for _ in range(200)]                   # 0,001 a 4,000, em milésimos
    assert sum(pedidos) > inicial                                                    # há déficit: parciais e negadas
    ids = await criar_requisicoes(SessionTeste, oid, mid, pedidos)

    await rodar_concorrente([processar(SessionTeste, rid, f"w{i % 8}") for i, rid in enumerate(ids)])

    reqs = await requisicoes_de(SessionTeste, mid)
    atendido = sum((r.quantidade_atendida for r in reqs), D(0))
    final = await estoque_de(SessionTeste, mid)
    assert final >= 0
    assert atendido + final == inicial                                               # nada vendido a mais, nada perdido
    assert {r.status for r in reqs} <= {S.APROVADA, S.PARCIAL, S.NEGADA}
    movs = await movimentacoes_de(SessionTeste, mid)
    assert sum((m.quantidade for m in movs), D(0)) == atendido
    saldo = inicial
    for m in movs:                                                                   # ordem do lock = ordem dos ids
        saldo -= m.quantidade
        assert m.saldo_apos == saldo
    assert saldo == final


# ---------------------------------------------------------------- API: JSON numérico e validação

async def test_api_devolve_numero_json_e_nao_texto(client, gestor):
    r = await client.post("/materiais", json={"nome": "Fio", "unidade": "m", "quantidade_estoque": 12.345,
                                              "estoque_critico": 0.1}, headers=gestor)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["quantidade_estoque"] == 12.345 and isinstance(corpo["quantidade_estoque"], float)
    assert corpo["estoque_critico"] == 0.1
    lista = (await client.get("/materiais", headers=gestor)).json()
    assert all(isinstance(m["quantidade_estoque"], (int, float)) for m in lista)


async def test_api_entrada_fracionada_soma_exato(client, gestor, dados):
    mid = dados["material"]["id"]                                                    # estoque inicial 100
    for _ in range(3):
        r = await client.post("/movimentacoes/entrada", json={"material_id": mid, "quantidade": 0.1}, headers=gestor)
        assert r.status_code == 201, r.text
    assert r.json()["quantidade_estoque"] == 100.3                                   # com float: 100.30000000000001
    movs = (await client.get(f"/movimentacoes?material_id={mid}", headers=gestor)).json()["itens"]
    assert movs[0]["saldo_apos"] == 100.3 and isinstance(movs[0]["quantidade"], float)


@pytest.mark.parametrize("valor", [1.2345, 123456789012.0])
async def test_api_recusa_mais_casas_ou_digitos_do_que_o_banco_guarda(client, gestor, dados, valor):
    r = await client.post("/movimentacoes/entrada", json={"material_id": dados["material"]["id"], "quantidade": valor},
                          headers=gestor)
    assert r.status_code == 422
    msg = r.json()["detail"][0]["msg"]
    assert msg.startswith(("deve ter no máximo 3 casa", "é grande demais"))         # em português
    assert str(valor) not in r.text                                                  # RN16: não devolve o valor enviado
