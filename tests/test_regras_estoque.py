"""Caso 1: regras básicas de alocação (camada de serviço)."""
import pytest

from app.models import StatusRequisicao as S
from tests.helpers import criar_material, criar_obra, criar_requisicoes, estoque_de, processar


async def _pedir(Sessao, estoque, pedido):
    mid = await criar_material(Sessao, estoque=estoque)
    oid = await criar_obra(Sessao)
    (rid,) = await criar_requisicoes(Sessao, oid, mid, [pedido])
    req = await processar(Sessao, rid)
    return req, await estoque_de(Sessao, mid)


@pytest.mark.parametrize("estoque,pedido,status,atendida,restante", [
    (100, 40, S.APROVADA, 40, 60),    # menor que o estoque
    (100, 100, S.APROVADA, 100, 0),   # igual ao estoque
    (100, 130, S.PARCIAL, 100, 0),    # maior: atende só o disponível
    (0, 30, S.NEGADA, 0, 0),          # estoque zerado
])
async def test_regras_basicas(SessionTeste, estoque, pedido, status, atendida, restante):
    req, final = await _pedir(SessionTeste, estoque, pedido)
    assert req.status == status
    assert req.quantidade_atendida == atendida
    assert final == restante
    assert req.processado_em is not None and req.worker_id == "w"


async def test_negada_nao_altera_estoque(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=0)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [5, 10])
    for rid in ids:
        assert (await processar(SessionTeste, rid)).status == S.NEGADA
    assert await estoque_de(SessionTeste, mid) == 0


async def test_requisicao_inexistente_levanta_erro(SessionTeste):
    with pytest.raises(ValueError):
        await processar(SessionTeste, "id-que-nao-existe")
