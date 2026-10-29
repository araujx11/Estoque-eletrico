"""Auditoria: o que é registrado, quem pode ver e a garantia de que segredos nunca são gravados."""
import json

from app.services.auditoria_service import _limpar
from tests.helpers import criar_material, criar_obra, criar_requisicoes


async def _log(client, headers, **filtros):
    r = await client.get("/auditoria", headers=headers, params={"tamanho": 200, **filtros})
    assert r.status_code == 200, r.text
    return r.json()


def test_limpar_remove_segredos_em_qualquer_nivel():
    sujo = {"email": "a@b.c", "senha": "x", "nested": {"nova_senha": "y", "ok": 1, "lista": [{"token": "t", "v": 2}]},
            "Authorization": "Bearer abc", "senha_hash": "h"}
    assert _limpar(sujo) == {"email": "a@b.c", "nested": {"ok": 1, "lista": [{"v": 2}]}}


async def test_login_com_sucesso_e_falha_sao_registrados_sem_a_senha(client, contas):
    await client.post("/auth/login", data={"username": "gestor@teste.com", "password": "senha-do-gestor"})
    await client.post("/auth/login", data={"username": "gestor@teste.com", "password": "SENHA-ERRADA-XYZ"})
    await client.post("/auth/login", data={"username": "ninguem@teste.com", "password": "OUTRA-SENHA-ERRADA"})

    log = await _log(client, contas["gestor"])
    acoes = [i["acao"] for i in log["itens"]]
    assert acoes.count("login") == 1 and acoes.count("login_falhou") == 2
    ok = next(i for i in log["itens"] if i["acao"] == "login")
    assert ok["usuario_nome"] == "Gestor" and ok["detalhes"] == {"email": "gestor@teste.com"}
    bruto = json.dumps(log)
    for segredo in ("senha-do-gestor", "SENHA-ERRADA-XYZ", "OUTRA-SENHA-ERRADA"):
        assert segredo not in bruto


async def test_usuarios_e_redefinicao_de_senha_nao_gravam_senha(client, contas):
    h = contas["gestor"]
    r = await client.post("/usuarios", headers=h, json={
        "nome": "Novo", "email": "novo@teste.com", "senha": "SENHA-INICIAL-777", "perfil": "almoxarife"})
    uid = r.json()["id"]
    await client.patch(f"/usuarios/{uid}", headers=h, json={"ativo": False})
    await client.post(f"/usuarios/{uid}/redefinir-senha", headers=h, json={"nova_senha": "NOVA-SENHA-888"})

    log = await _log(client, h, entidade="usuario")
    acoes = {i["acao"] for i in log["itens"]}
    assert {"usuario_criado", "usuario_editado", "senha_redefinida"} <= acoes
    editado = next(i for i in log["itens"] if i["acao"] == "usuario_editado")
    assert editado["detalhes"]["antes"]["ativo"] is True and editado["detalhes"]["depois"]["ativo"] is False
    tudo = json.dumps(await _log(client, h))
    assert "SENHA-INICIAL-777" not in tudo and "NOVA-SENHA-888" not in tudo and "senha_hash" not in tudo


async def test_acoes_de_estoque_obra_e_requisicao_sao_registradas(client, contas):
    h, mid, oa = contas["gestor"], contas["material"]["id"], contas["obra_a"]["id"]
    await client.post("/materiais", headers=h, json={"nome": "Novo material", "unidade": "un", "quantidade_estoque": 5})
    await client.post("/obras", headers=h, json={"nome": "Obra Z", "cidade": "Olinda"})
    await client.post("/movimentacoes/entrada", headers=h, json={"material_id": mid, "quantidade": 10, "observacao": "NF 1"})
    await client.post("/movimentacoes/ajuste", headers=h, json={"material_id": mid, "quantidade": -2, "observacao": "quebra"})
    await client.post("/requisicoes", headers=h, json={"obra_id": oa, "material_id": mid, "quantidade_solicitada": 3})
    await client.post("/pedidos", headers=h, json={"obra_id": oa, "itens": [{"material_id": mid, "quantidade_solicitada": 1}]})

    acoes = {i["acao"] for i in (await _log(client, h))["itens"]}
    assert {"material_criado", "obra_criada", "entrada_estoque", "ajuste_estoque", "requisicao_criada", "pedido_criado"} <= acoes
    entrada = (await _log(client, h, acao="entrada_estoque"))["itens"][0]
    assert entrada["detalhes"]["quantidade"] == 10 and entrada["detalhes"]["saldo_apos"] == 110
    assert entrada["entidade"] == "material" and entrada["entidade_id"] == str(mid)


async def test_remocoes_e_edicoes_sao_registradas(client, contas, SessionTeste):
    h = contas["gestor"]
    mid = await criar_material(SessionTeste, "Descartável", estoque=1)
    oid = await criar_obra(SessionTeste, "Obra descartável")
    await client.put(f"/materiais/{contas['material']['id']}", headers=h, json={
        "nome": "Cabo 10mm2", "unidade": "m", "quantidade_estoque": 100, "estoque_critico": 33})
    await client.patch(f"/obras/{oid}/andamento", headers=h, json={"progresso": 30})
    assert (await client.delete(f"/materiais/{mid}", headers=h)).status_code == 204
    assert (await client.delete(f"/obras/{oid}", headers=h)).status_code == 204
    acoes = {i["acao"] for i in (await _log(client, h))["itens"]}
    assert {"material_editado", "obra_andamento", "material_removido", "obra_removida"} <= acoes


async def test_filtros_e_paginacao_da_auditoria(client, contas):
    h, mid = contas["gestor"], contas["material"]["id"]
    for _ in range(5):
        await client.post("/movimentacoes/entrada", headers=h, json={"material_id": mid, "quantidade": 1})
    so = await _log(client, h, acao="entrada_estoque")
    assert so["total"] == 5 and all(i["acao"] == "entrada_estoque" for i in so["itens"])
    p1 = await _log(client, h, acao="entrada_estoque", tamanho=2, pagina=1)
    p3 = await _log(client, h, acao="entrada_estoque", tamanho=2, pagina=3)
    assert (len(p1["itens"]), len(p3["itens"]), p1["total"]) == (2, 1, 5)
    eu = (await client.get("/auth/me", headers=h)).json()["id"]
    assert (await _log(client, h, usuario_id=eu, acao="entrada_estoque"))["total"] == 5
    assert (await _log(client, h, usuario_id=eu + 999))["total"] == 0
    assert (await _log(client, h, de="2999-01-01"))["total"] == 0
    assert "entrada_estoque" in (await client.get("/auditoria/acoes", headers=h)).json()


async def test_somente_o_gestor_acessa_a_auditoria(client, contas):
    for rota in ("/auditoria", "/auditoria/acoes"):
        assert (await client.get(rota)).status_code == 401
        assert (await client.get(rota, headers=contas["almox"])).status_code == 403
        assert (await client.get(rota, headers=contas["eng"])).status_code == 403
        assert (await client.get(rota, headers=contas["gestor"])).status_code == 200
