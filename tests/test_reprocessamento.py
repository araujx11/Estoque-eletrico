"""
Caso 7 (reprocessamento) e comportamento após reinício da API.

Os testes marcados com xfail(strict=True) descrevem o comportamento DESEJADO e falham hoje:
documentam ACHADOS em docs/achados.md. Quando a correção for aplicada eles passam a dar XPASS
e o pytest acusa erro (strict), lembrando de remover o marcador.
Para ver os números reais do achado: pytest tests/test_reprocessamento.py --runxfail
"""
import asyncio

import pytest

from app.models import StatusRequisicao as S
from app.services.fila_service import fila_global, reenfileirar_pendentes
from tests.helpers import criar_material, criar_obra, criar_requisicoes, estoque_de, processar, rodar_concorrente


@pytest.mark.xfail(strict=True, reason="ACHADO 1: processar_requisicao não é idempotente (debita de novo)")
async def test_reprocessar_a_mesma_requisicao_nao_debita_duas_vezes(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])

    primeira = await processar(SessionTeste, rid)
    assert primeira.status == S.APROVADA and await estoque_de(SessionTeste, mid) == 70
    await processar(SessionTeste, rid)            # segunda chamada para a MESMA requisição

    assert await estoque_de(SessionTeste, mid) == 70, "o estoque foi debitado duas vezes"


@pytest.mark.xfail(strict=True, reason="ACHADO 1: a mesma requisição em 2 workers ao mesmo tempo debita 2x")
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
