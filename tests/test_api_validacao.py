"""Caso 8: entradas inválidas nas rotas de requisição."""
import pytest


def _corpo(dados, **extra):
    base = {"obra_id": dados["obra_a"]["id"], "material_id": dados["material"]["id"], "quantidade_solicitada": 5}
    return {**base, **extra}


async def test_obra_inexistente_da_404(client, gestor, dados):
    r = await client.post("/requisicoes", headers=gestor, json=_corpo(dados, obra_id=99999))
    assert r.status_code == 404 and "Obra" in r.json()["detail"]


async def test_material_inexistente_da_404(client, gestor, dados):
    r = await client.post("/requisicoes", headers=gestor, json=_corpo(dados, material_id=99999))
    assert r.status_code == 404 and "Material" in r.json()["detail"]


async def test_pedido_com_material_inexistente_da_404(client, gestor, dados):
    r = await client.post("/pedidos", headers=gestor, json={
        "obra_id": dados["obra_a"]["id"],
        "itens": [{"material_id": dados["material"]["id"], "quantidade_solicitada": 1},
                  {"material_id": 99999, "quantidade_solicitada": 1}]})
    assert r.status_code == 404
    # nada foi criado pela metade
    assert (await client.get("/requisicoes", headers=gestor)).json() == []


async def test_engenheiro_obra_inexistente_da_403_sem_vazar_existencia(client, eng, dados):
    r = await client.post("/requisicoes", headers=eng, json=_corpo(dados, obra_id=99999))
    assert r.status_code == 403


@pytest.mark.parametrize("qtd", [0, -1, -0.5])
async def test_quantidade_nao_positiva_da_422(client, gestor, dados, qtd):
    r = await client.post("/requisicoes", headers=gestor, json=_corpo(dados, quantidade_solicitada=qtd))
    assert r.status_code == 422
    r = await client.post("/pedidos", headers=gestor, json={
        "obra_id": dados["obra_a"]["id"],
        "itens": [{"material_id": dados["material"]["id"], "quantidade_solicitada": qtd}]})
    assert r.status_code == 422


async def test_campos_ausentes_ou_tipos_errados_dao_422(client, gestor, dados):
    assert (await client.post("/requisicoes", headers=gestor, json={})).status_code == 422
    assert (await client.post("/requisicoes", headers=gestor, json=_corpo(dados, quantidade_solicitada="muito"))).status_code == 422
    assert (await client.post("/pedidos", headers=gestor, json={"obra_id": dados["obra_a"]["id"], "itens": []})).status_code == 422


async def test_pedido_com_material_repetido_da_422(client, gestor, dados):
    item = {"material_id": dados["material"]["id"], "quantidade_solicitada": 1}
    r = await client.post("/pedidos", headers=gestor, json={"obra_id": dados["obra_a"]["id"], "itens": [item, item]})
    assert r.status_code == 422


async def test_obra_finalizada_nao_aceita_requisicao(client, gestor, dados):
    oid = dados["obra_a"]["id"]
    assert (await client.patch(f"/obras/{oid}/andamento", headers=gestor,
                               json={"progresso": 100, "finalizada": True})).status_code == 200
    r = await client.post("/requisicoes", headers=gestor, json=_corpo(dados))
    assert r.status_code == 409


async def test_requisicao_inexistente_da_404(client, gestor):
    assert (await client.get("/requisicoes/nao-existe", headers=gestor)).status_code == 404
