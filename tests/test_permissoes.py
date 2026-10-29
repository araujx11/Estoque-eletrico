import pytest

ROTAS_PROTEGIDAS = [
    ("get", "/materiais"), ("get", "/obras"), ("get", "/requisicoes"), ("get", "/usuarios"),
    ("get", "/unidades"), ("get", "/resumo"), ("get", "/dashboard/resumo"),
    ("post", "/materiais"), ("post", "/obras"), ("post", "/requisicoes"), ("post", "/pedidos"),
    ("post", "/usuarios"), ("delete", "/materiais/1"), ("delete", "/obras/1"),
    ("get", "/diagnostico/deficit"), ("post", "/diagnostico/simular"), ("get", "/movimentacoes"),
    ("post", "/movimentacoes/entrada"), ("post", "/movimentacoes/ajuste"), ("get", "/auditoria"),
    ("get", "/dashboard/completo"), ("get", "/relatorios/consumo"), ("put", "/materiais/1"),
]


@pytest.mark.parametrize("metodo,rota", ROTAS_PROTEGIDAS)
async def test_sem_token_da_401(client, metodo, rota):
    r = await getattr(client, metodo)(rota)
    assert r.status_code == 401, f"{metodo.upper()} {rota} -> {r.status_code}"


# ---------- engenheiro ----------

async def test_engenheiro_nao_acessa_usuarios(client, eng):
    assert (await client.get("/usuarios", headers=eng)).status_code == 403
    assert (await client.post("/usuarios", headers=eng, json={
        "nome": "X", "email": "x@x.com", "senha": "senha-12345", "perfil": "gestor"})).status_code == 403


async def test_engenheiro_so_le_materiais(client, eng, dados):
    assert (await client.get("/materiais", headers=eng)).status_code == 200
    novo = {"nome": "Disjuntor", "unidade": "un", "quantidade_estoque": 5}
    assert (await client.post("/materiais", json=novo, headers=eng)).status_code == 403
    mid = dados["material"]["id"]
    assert (await client.put(f"/materiais/{mid}", json={**novo, "nome": "Cabo 10mm2"}, headers=eng)).status_code == 403
    assert (await client.delete(f"/materiais/{mid}", headers=eng)).status_code == 403


async def test_engenheiro_nao_gerencia_obras(client, eng):
    assert (await client.post("/obras", json={"nome": "N", "cidade": "C"}, headers=eng)).status_code == 403
    assert (await client.delete("/obras/1", headers=eng)).status_code == 403


async def test_engenheiro_ve_so_a_propria_obra(client, eng, dados):
    obras = (await client.get("/obras", headers=eng)).json()
    assert [o["id"] for o in obras] == [dados["obra_a"]["id"]]


async def test_engenheiro_nao_pede_para_outra_obra(client, eng, dados):
    mat = dados["material"]["id"]
    r = (await client.post("/requisicoes", headers=eng, json={
        "obra_id": dados["obra_b"]["id"], "material_id": mat, "quantidade_solicitada": 1}))
    assert r.status_code == 403
    r = (await client.post("/pedidos", headers=eng, json={
        "obra_id": dados["obra_b"]["id"], "itens": [{"material_id": mat, "quantidade_solicitada": 1}]}))
    assert r.status_code == 403


async def test_engenheiro_pede_para_propria_obra_e_registra_solicitante(client, eng, dados):
    r = (await client.post("/requisicoes", headers=eng, json={
        "obra_id": dados["obra_a"]["id"], "material_id": dados["material"]["id"], "quantidade_solicitada": 2}))
    assert r.status_code == 202, r.text
    eu = (await client.get("/auth/me", headers=eng)).json()
    assert r.json()["solicitante_id"] == eu["id"]


async def test_quantidade_deve_ser_positiva(client, eng, dados):
    for qtd in (0, -5):
        r = (await client.post("/requisicoes", headers=eng, json={
            "obra_id": dados["obra_a"]["id"], "material_id": dados["material"]["id"], "quantidade_solicitada": qtd}))
        assert r.status_code == 422


async def test_engenheiro_so_ve_requisicoes_da_sua_obra(client, eng, gestor, dados):
    mat = dados["material"]["id"]
    (await client.post("/requisicoes", headers=gestor, json={
        "obra_id": dados["obra_b"]["id"], "material_id": mat, "quantidade_solicitada": 1}))
    (await client.post("/requisicoes", headers=eng, json={
        "obra_id": dados["obra_a"]["id"], "material_id": mat, "quantidade_solicitada": 1}))
    visiveis = (await client.get("/requisicoes", headers=eng)).json()
    assert len(visiveis) == 1 and all(r["obra_id"] == dados["obra_a"]["id"] for r in visiveis)

    todas = (await client.get("/requisicoes", headers=gestor)).json()
    da_b = next(r for r in todas if r["obra_id"] == dados["obra_b"]["id"])
    assert (await client.get(f"/requisicoes/{da_b['id']}", headers=eng)).status_code == 403


async def test_dashboard_do_engenheiro_so_conta_a_propria_obra(client, eng, gestor, dados):
    mat = dados["material"]["id"]
    for obra in ("obra_a", "obra_b", "obra_b"):  # 1 da obra do engenheiro + 2 de outra obra
        (await client.post("/requisicoes", headers=gestor, json={
            "obra_id": dados[obra]["id"], "material_id": mat, "quantidade_solicitada": 1}))
    d_eng = (await client.get("/dashboard/resumo", headers=eng)).json()
    d_ges = (await client.get("/dashboard/resumo", headers=gestor)).json()
    assert d_eng["total_obras"] == 1
    assert d_ges["total_obras"] == 2
    assert d_eng["total_requisicoes"] == 1 and d_ges["total_requisicoes"] == 3


# ---------- almoxarife ----------

async def test_almoxarife_nao_gerencia_usuarios(client, almox):
    assert (await client.get("/usuarios", headers=almox)).status_code == 403
    assert (await client.post("/usuarios", headers=almox, json={
        "nome": "X", "email": "y@x.com", "senha": "senha-12345", "perfil": "gestor"})).status_code == 403
    assert (await client.patch("/usuarios/1", headers=almox, json={"ativo": False})).status_code == 403
    assert (await client.post("/usuarios/1/redefinir-senha", headers=almox, json={"nova_senha": "12345678"})).status_code == 403


async def test_almoxarife_cadastra_edita_material_e_ve_tudo(client, almox, dados):
    r = (await client.post("/materiais", headers=almox, json={
        "nome": "Eletroduto", "unidade": "barra", "quantidade_estoque": 30, "estoque_critico": 5}))
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    r = (await client.put(f"/materiais/{mid}", headers=almox, json={
        "nome": "Eletroduto", "unidade": "barra", "quantidade_estoque": 80, "estoque_critico": 5}))
    assert r.status_code == 200 and r.json()["quantidade_estoque"] == 80
    assert len((await client.get("/obras", headers=almox)).json()) >= 2
    assert (await client.get("/requisicoes", headers=almox)).status_code == 200
    assert (await client.delete(f"/materiais/{mid}", headers=almox)).status_code == 403  # remoção: só gestor


async def test_almoxarife_nao_cria_obra_nem_requisicao(client, almox, dados):
    assert (await client.post("/obras", json={"nome": "N", "cidade": "C"}, headers=almox)).status_code == 403
    r = (await client.post("/requisicoes", headers=almox, json={
        "obra_id": dados["obra_a"]["id"], "material_id": dados["material"]["id"], "quantidade_solicitada": 1}))
    assert r.status_code == 403


# ---------- gestor ----------

async def test_gestor_tem_acesso_total(client, gestor, dados):
    assert (await client.get("/usuarios", headers=gestor)).status_code == 200
    r = (await client.post("/requisicoes", headers=gestor, json={
        "obra_id": dados["obra_b"]["id"], "material_id": dados["material"]["id"], "quantidade_solicitada": 1}))
    assert r.status_code == 202
