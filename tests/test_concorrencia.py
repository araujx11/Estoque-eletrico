"""Casos 2, 3 e 4: correção do estoque sob concorrência (várias sessões disputando as mesmas linhas)."""
import random

import pytest

from app.models import StatusRequisicao as S
from tests.helpers import (
    criar_material, criar_obra, criar_requisicoes, estoque_de, processar, requisicoes_de, rodar_concorrente,
)


async def test_cenario_do_enunciado_100m_com_40_30_50(SessionTeste):
    mid = await criar_material(SessionTeste, "Cabo 10mm2", estoque=100)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [40, 30, 50])

    # as 3 ao mesmo tempo, cada uma na sua sessão/conexão
    await rodar_concorrente([processar(SessionTeste, rid, f"worker-{i}") for i, rid in enumerate(ids)])

    reqs = await requisicoes_de(SessionTeste, mid)
    final = await estoque_de(SessionTeste, mid)
    assert sum(r.quantidade_atendida for r in reqs) == 100
    assert final == 0 and final >= 0
    assert sum(r.status == S.PARCIAL for r in reqs) == 1
    assert sum(r.status == S.APROVADA for r in reqs) == 2
    assert all(r.quantidade_atendida <= r.quantidade_solicitada for r in reqs)


@pytest.mark.stress
async def test_estresse_200_requisicoes_no_mesmo_material(SessionTeste):
    rng = random.Random(42)                       # semente fixa: carga reproduzível
    inicial = 1000
    quantidades = [rng.randint(1, 30) for _ in range(200)]
    assert sum(quantidades) > inicial             # há déficit: parciais/negadas acontecem

    mid = await criar_material(SessionTeste, estoque=inicial)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, quantidades)

    await rodar_concorrente([processar(SessionTeste, rid, f"w{i % 8}") for i, rid in enumerate(ids)])

    reqs = await requisicoes_de(SessionTeste, mid)
    final = await estoque_de(SessionTeste, mid)
    atendido = sum(r.quantidade_atendida for r in reqs)
    assert len(reqs) == 200 and all(r.status not in (S.PENDENTE, S.EM_PROCESSAMENTO, S.ERRO) for r in reqs)
    assert final >= 0                                             # nunca negativo
    assert inicial == final + atendido                            # conservação
    assert all(r.quantidade_atendida <= r.quantidade_solicitada for r in reqs)
    assert final == 0 and atendido == inicial                     # a demanda excede o estoque: tudo vendido


async def test_materiais_diferentes_nao_interferem(SessionTeste):
    oid = await criar_obra(SessionTeste)
    a = await criar_material(SessionTeste, "A", estoque=100)   # demanda 60  -> sobra 40, todas aprovadas
    b = await criar_material(SessionTeste, "B", estoque=50)    # demanda 80  -> zera, atende 50
    c = await criar_material(SessionTeste, "C", estoque=0)     # sem estoque -> todas negadas
    ids_a = await criar_requisicoes(SessionTeste, oid, a, [10] * 6)
    ids_b = await criar_requisicoes(SessionTeste, oid, b, [20] * 4)
    ids_c = await criar_requisicoes(SessionTeste, oid, c, [7] * 5)

    todos = ids_a + ids_b + ids_c
    random.Random(1).shuffle(todos)               # intercala os três materiais na disputa
    await rodar_concorrente([processar(SessionTeste, rid) for rid in todos])

    ra, rb, rc = [await requisicoes_de(SessionTeste, m) for m in (a, b, c)]
    assert await estoque_de(SessionTeste, a) == 40 and all(r.status == S.APROVADA for r in ra)
    assert await estoque_de(SessionTeste, b) == 0 and sum(r.quantidade_atendida for r in rb) == 50
    assert await estoque_de(SessionTeste, c) == 0
    assert all(r.status == S.NEGADA and r.quantidade_atendida == 0 for r in rc)
