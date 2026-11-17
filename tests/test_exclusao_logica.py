"""Achado 11: exclusão lógica de materiais e obras. Desativar preserva o histórico; reativar volta ao normal."""
from sqlalchemy import func, select

from app.models import LogAuditoria, Material, MovimentacaoEstoque, Obra, Requisicao, StatusRequisicao as S
from app.services.fila_service import FilaRequisicoes, reenfileirar_pendentes
from tests.helpers import criar_obra, criar_requisicoes, processar


def _pedido(dados, qtd=5, material=None):
    return {"obra_id": dados["obra_a"]["id"], "material_id": (material or dados["material"])["id"], "quantidade_solicitada": qtd}


async def _acoes(client, gestor, acao):
    r = await client.get(f"/auditoria?acao={acao}", headers=gestor)
    return r.json()["itens"]


# ---------------------------------------------------------------- desativar mantém o histórico

async def test_desativar_material_mantem_historico_e_some_das_listagens(client, gestor, almox, eng, dados, SessionTeste):
    mat = dados["material"]
    r = await client.post("/requisicoes", json=_pedido(dados, 10), headers=eng)
    assert r.status_code == 202
    await processar(SessionTeste, r.json()["id"])                           # gera requisição resolvida + saída no livro-razão

    assert (await client.delete(f"/materiais/{mat['id']}", headers=gestor)).status_code == 204

    async with SessionTeste() as db:                                         # a linha e todo o histórico continuam no banco
        m = await db.get(Material, mat["id"])
        assert m is not None and m.ativo is False and m.desativado_em is not None and m.nome == mat["nome"]
        assert (await db.execute(select(func.count()).select_from(Requisicao).where(Requisicao.material_id == mat["id"]))).scalar_one() == 1
        assert (await db.execute(select(func.count()).select_from(MovimentacaoEstoque)
                                 .where(MovimentacaoEstoque.material_id == mat["id"]))).scalar_one() == 2   # estoque inicial + saída
    assert (await _acoes(client, gestor, "material_desativado"))[0]["entidade_id"] == str(mat["id"])
    assert (await _acoes(client, gestor, "material_criado"))                # a auditoria antiga permanece

    assert mat["id"] not in [m["id"] for m in (await client.get("/materiais", headers=gestor)).json()]
    todos = (await client.get("/materiais?incluir_inativos=true", headers=gestor)).json()
    assert [m["ativo"] for m in todos if m["id"] == mat["id"]] == [False]
    for h in (almox, eng):                                                   # o parâmetro só vale para o gestor
        lista = (await client.get("/materiais?incluir_inativos=true", headers=h)).json()
        assert mat["id"] not in [m["id"] for m in lista]
    historico = (await client.get(f"/movimentacoes?material_id={mat['id']}", headers=gestor)).json()
    assert historico["total"] == 2                                           # o histórico continua consultável


async def test_desativar_obra_mantem_historico_e_some_das_listagens(client, gestor, dados, SessionTeste):
    obra = dados["obra_b"]                                                   # sem engenheiro vinculado
    (rid,) = await criar_requisicoes(SessionTeste, obra["id"], dados["material"]["id"], [5])
    await processar(SessionTeste, rid)

    assert (await client.delete(f"/obras/{obra['id']}", headers=gestor)).status_code == 204
    async with SessionTeste() as db:
        o = await db.get(Obra, obra["id"])
        assert o.ativo is False and o.desativado_em is not None
        assert (await db.get(Requisicao, rid)).status == S.APROVADA          # a requisição e o consumo permanecem
    assert obra["id"] not in [o["id"] for o in (await client.get("/obras", headers=gestor)).json()]
    assert obra["id"] in [o["id"] for o in (await client.get("/obras?incluir_inativos=true", headers=gestor)).json()]
    consumo = (await client.get("/relatorios/consumo", headers=gestor)).json()["itens"]
    assert any(i["obra"] == obra["nome"] for i in consumo)                   # o relatório de consumo mantém o histórico
    assert (await _acoes(client, gestor, "obra_desativada"))


async def test_desativar_e_idempotente(client, gestor, dados):
    mid = dados["material"]["id"]
    assert (await client.delete(f"/materiais/{mid}", headers=gestor)).status_code == 204
    assert (await client.delete(f"/materiais/{mid}", headers=gestor)).status_code == 204
    assert len(await _acoes(client, gestor, "material_desativado")) == 1


async def test_nao_desativa_com_requisicao_aberta(client, gestor, dados, SessionTeste):
    await criar_requisicoes(SessionTeste, dados["obra_a"]["id"], dados["material"]["id"], [5])      # pendente
    r = await client.delete(f"/materiais/{dados['material']['id']}", headers=gestor)
    assert r.status_code == 409 and "pendente" in r.json()["detail"]
    r = await client.delete(f"/obras/{dados['obra_b']['id']}", headers=gestor)                       # obra_b não tem pedido: ok
    assert r.status_code == 204


# ---------------------------------------------------------------- inativo não aceita novos pedidos nem entradas

async def test_material_inativo_recusa_pedido_e_entrada(client, gestor, eng, dados):
    mid = dados["material"]["id"]
    await client.delete(f"/materiais/{mid}", headers=gestor)
    r = await client.post("/requisicoes", json=_pedido(dados), headers=eng)
    assert r.status_code == 409 and "desativado" in r.json()["detail"]
    r = await client.post("/pedidos", json={"obra_id": dados["obra_a"]["id"], "itens": [{"material_id": mid, "quantidade_solicitada": 1}]}, headers=eng)
    assert r.status_code == 409 and "desativado" in r.json()["detail"]
    r = await client.post("/movimentacoes/entrada", json={"material_id": mid, "quantidade": 5}, headers=gestor)
    assert r.status_code == 409 and "desativado" in r.json()["detail"]
    r = await client.put(f"/materiais/{mid}", json={"nome": "Outro nome", "unidade": "m", "quantidade_estoque": 100}, headers=gestor)
    assert r.status_code == 409


async def test_obra_inativa_recusa_pedido_e_nao_aceita_novos_usuarios(client, gestor, dados):
    oid = dados["obra_b"]["id"]
    await client.delete(f"/obras/{oid}", headers=gestor)
    r = await client.post("/requisicoes", json={"obra_id": oid, "material_id": dados["material"]["id"], "quantidade_solicitada": 1}, headers=gestor)
    assert r.status_code == 409 and "desativada" in r.json()["detail"]
    r = await client.post("/usuarios", json={"nome": "Novo", "email": "novo@t.com", "senha": "senha-12345", "perfil": "engenheiro", "obra_id": oid}, headers=gestor)
    assert r.status_code == 409


async def test_inativo_nao_entra_na_fila(SessionTeste):
    from tests.helpers import criar_material
    mid, oid = await criar_material(SessionTeste, estoque=100), await criar_obra(SessionTeste)
    await criar_requisicoes(SessionTeste, oid, mid, [1, 2])
    async with SessionTeste() as db:                                         # simula a corrida: desativado com pedidos já abertos
        (await db.get(Material, mid)).ativo = False
        await db.commit()
    fila = FilaRequisicoes()
    assert await reenfileirar_pendentes(fila, SessionTeste, "A") == 0 and fila.tamanho() == 0


async def test_diagnostico_e_dashboard_ignoram_inativos(client, gestor, dados):
    await client.delete(f"/materiais/{dados['material']['id']}", headers=gestor)
    nomes = [m["nome"] for m in (await client.get("/diagnostico/deficit", headers=gestor)).json()["materiais"]]
    assert dados["material"]["nome"] not in nomes
    assert (await client.get("/dashboard/completo", headers=gestor)).json()["cards"]["total_materiais"] == 0
    r = await client.post("/diagnostico/simular", json={"requisicoes": [{"obra_id": dados["obra_a"]["id"], "material_id": dados["material"]["id"], "quantidade": 1}]}, headers=gestor)
    assert r.status_code == 404


# ---------------------------------------------------------------- reativar (e nome único)

async def test_reativar_material_volta_ao_normal(client, gestor, eng, dados):
    mat = dados["material"]
    await client.delete(f"/materiais/{mat['id']}", headers=gestor)
    r = await client.patch(f"/materiais/{mat['id']}", json={"ativo": True}, headers=gestor)
    assert r.status_code == 200 and r.json()["ativo"] is True and r.json()["desativado_em"] is None
    assert r.json()["quantidade_estoque"] == 100                             # o saldo é o mesmo, nada foi recriado
    assert mat["id"] in [m["id"] for m in (await client.get("/materiais", headers=gestor)).json()]
    assert (await client.post("/requisicoes", json=_pedido(dados), headers=eng)).status_code == 202
    assert (await client.post("/movimentacoes/entrada", json={"material_id": mat["id"], "quantidade": 5}, headers=gestor)).status_code == 201
    assert (await _acoes(client, gestor, "material_reativado"))


async def test_reativar_obra_volta_ao_normal(client, gestor, dados):
    oid = dados["obra_b"]["id"]
    await client.delete(f"/obras/{oid}", headers=gestor)
    r = await client.patch(f"/obras/{oid}", json={"ativo": True}, headers=gestor)
    assert r.status_code == 200 and r.json()["ativo"] is True
    assert oid in [o["id"] for o in (await client.get("/obras", headers=gestor)).json()]
    r = await client.post("/requisicoes", json={"obra_id": oid, "material_id": dados["material"]["id"], "quantidade_solicitada": 1}, headers=gestor)
    assert r.status_code == 202
    assert (await _acoes(client, gestor, "obra_reativada"))


async def test_nome_continua_unico_e_a_mensagem_manda_reativar(client, gestor, dados):
    mat = dados["material"]
    await client.delete(f"/materiais/{mat['id']}", headers=gestor)
    r = await client.post("/materiais", json={"nome": mat["nome"], "unidade": "m", "quantidade_estoque": 1}, headers=gestor)
    assert r.status_code == 409 and "Reative" in r.json()["detail"]


# ---------------------------------------------------------------- permissões e erros

async def test_so_o_gestor_desativa_e_reativa(client, almox, eng, gestor, dados):
    mid, oid = dados["material"]["id"], dados["obra_b"]["id"]
    for h in (almox, eng):
        assert (await client.delete(f"/materiais/{mid}", headers=h)).status_code == 403
        assert (await client.delete(f"/obras/{oid}", headers=h)).status_code == 403
        assert (await client.patch(f"/materiais/{mid}", json={"ativo": True}, headers=h)).status_code == 403
        assert (await client.patch(f"/obras/{oid}", json={"ativo": True}, headers=h)).status_code == 403
    assert (await client.patch(f"/materiais/{mid}", json={"ativo": True})).status_code == 401


async def test_inexistentes_e_validacao(client, gestor):
    assert (await client.delete("/materiais/99999", headers=gestor)).status_code == 404
    assert (await client.patch("/materiais/99999", json={"ativo": True}, headers=gestor)).status_code == 404
    assert (await client.patch("/obras/99999", json={"ativo": True}, headers=gestor)).status_code == 404
    assert (await client.patch("/materiais/1", json={}, headers=gestor)).status_code == 422
