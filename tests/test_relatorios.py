"""
Relatórios, exportação CSV e dashboard completo, sobre um conjunto PEQUENO e conhecido de dados.

Dados (obra A = obra do engenheiro; obra B = outra):
  r1  A  Cabo        pediu 10  recebeu 10  aprovada   10/03  worker-1  espera 2 s
  r2  A  Cabo        pediu 20  recebeu  5  parcial    11/03  worker-2  espera 4 s
  r3  B  Cabo        pediu 10  recebeu  0  negada     11/03  worker-1  espera 6 s
  r4  B  Disjuntor   pediu  4  recebeu  4  aprovada   12/03  worker-1  espera 8 s
  r5  A  Disjuntor   pediu  3  (pendente)             12/03
Materiais críticos: Cabo (3 de 10 = 30%), Fita (1,5 de 3 = 50%), "=SOMA(A1)" (0 de 1).
"""
import csv
import io
from datetime import datetime, timedelta

from app.models import StatusRequisicao as S
from tests.helpers import criar_material, criar_requisicao

D1, D2, D3 = datetime(2026, 3, 10, 12), datetime(2026, 3, 11, 12), datetime(2026, 3, 12, 12)


async def _dados(contas, SessionTeste):
    A, B = contas["obra_a"]["id"], contas["obra_b"]["id"]
    cabo = await criar_material(SessionTeste, "Cabo", estoque=3, critico=10, unidade="m")
    disj = await criar_material(SessionTeste, "Disjuntor", estoque=50, critico=5, unidade="un")
    fita = await criar_material(SessionTeste, "Fita", estoque=1.5, critico=3, unidade="rolo")
    await criar_material(SessionTeste, "=SOMA(A1)", estoque=0, critico=1, unidade="un")
    seg = timedelta(seconds=1)
    await criar_requisicao(SessionTeste, A, cabo, 10, atendida=10, status=S.APROVADA, criado_em=D1, processado_em=D1 + 2 * seg, worker_id="worker-1")
    await criar_requisicao(SessionTeste, A, cabo, 20, atendida=5, status=S.PARCIAL, criado_em=D2, processado_em=D2 + 4 * seg, worker_id="worker-2")
    await criar_requisicao(SessionTeste, B, cabo, 10, atendida=0, status=S.NEGADA, criado_em=D2, processado_em=D2 + 6 * seg, worker_id="worker-1")
    await criar_requisicao(SessionTeste, B, disj, 4, atendida=4, status=S.APROVADA, criado_em=D3, processado_em=D3 + 8 * seg, worker_id="worker-1")
    await criar_requisicao(SessionTeste, A, disj, 3, status=S.PENDENTE, criado_em=D3)
    return {"A": A, "B": B, "cabo": cabo, "disj": disj, "fita": fita}


async def _get(client, headers, rota, **params):
    r = await client.get(rota, headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _linhas(tabela, *chaves):
    return [tuple(i[c] for c in chaves) for i in tabela["itens"]]


# ---------------------------------------------------------------- 1) consumo

async def test_consumo_por_obra_e_material(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    t = await _get(client, contas["gestor"], "/relatorios/consumo")
    # padrão: só pedidos aprovados/parciais, do maior para o menor consumo
    assert _linhas(t, "obra", "material", "unidade", "quantidade_atendida", "quantidade_solicitada", "requisicoes") == [
        ("Obra A", "Cabo", "m", 15, 30, 2), ("Obra B", "Disjuntor", "un", 4, 4, 1)]
    assert t["total"] == 2 and [c["chave"] for c in t["colunas"]][:3] == ["obra", "material", "unidade"]


async def test_consumo_agrupado_por_material_e_por_obra(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    por_mat = await _get(client, contas["gestor"], "/relatorios/consumo", agrupar="material")
    assert _linhas(por_mat, "material", "quantidade_atendida", "requisicoes") == [("Cabo", 15, 2), ("Disjuntor", 4, 1)]
    por_obra = await _get(client, contas["gestor"], "/relatorios/consumo", agrupar="obra", ordenar="obra", direcao="asc")
    assert _linhas(por_obra, "obra", "itens_atendidos", "materiais_distintos") == [("Obra A", 2, 1), ("Obra B", 1, 1)]


async def test_consumo_filtros(client, contas, SessionTeste):
    ids = await _dados(contas, SessionTeste)
    h = contas["gestor"]
    assert _linhas(await _get(client, h, "/relatorios/consumo", de="2026-03-11"), "obra", "quantidade_atendida") == [("Obra A", 5), ("Obra B", 4)]
    assert _linhas(await _get(client, h, "/relatorios/consumo", ate="2026-03-10"), "obra", "quantidade_atendida") == [("Obra A", 10)]
    assert _linhas(await _get(client, h, "/relatorios/consumo", obra_id=ids["B"]), "obra", "material") == [("Obra B", "Disjuntor")]
    assert _linhas(await _get(client, h, "/relatorios/consumo", material_id=ids["cabo"]), "obra", "quantidade_atendida") == [("Obra A", 15)]
    negadas = await _get(client, h, "/relatorios/consumo", status="negada")
    assert _linhas(negadas, "obra", "material", "quantidade_atendida", "requisicoes") == [("Obra B", "Cabo", 0, 1)]


async def test_ordenacao_paginacao_e_validacao(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    h = contas["gestor"]
    asc = await _get(client, h, "/relatorios/consumo", ordenar="material", direcao="asc")
    assert [i["material"] for i in asc["itens"]] == ["Cabo", "Disjuntor"]
    p2 = await _get(client, h, "/relatorios/consumo", tamanho=1, pagina=2)
    assert (p2["total"], p2["pagina"], len(p2["itens"]), p2["itens"][0]["material"]) == (2, 2, 1, "Disjuntor")
    assert (await client.get("/relatorios/consumo", headers=h, params={"ordenar": "sql; drop"})).status_code == 422
    assert (await client.get("/relatorios/consumo", headers=h, params={"tamanho": 0})).status_code == 422
    assert (await client.get("/relatorios/consumo", headers=h, params={"agrupar": "x"})).status_code == 422


# ---------------------------------------------------------------- 2) requisições por período

async def test_requisicoes_por_dia(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    t = await _get(client, contas["gestor"], "/relatorios/requisicoes-por-periodo")
    assert _linhas(t, "periodo", "total", "aprovada", "parcial", "negada", "pendente") == [
        ("2026-03-10", 1, 1, 0, 0, 0), ("2026-03-11", 2, 0, 1, 1, 0), ("2026-03-12", 2, 1, 0, 0, 1)]
    filtrado = await _get(client, contas["gestor"], "/relatorios/requisicoes-por-periodo", de="2026-03-11", ate="2026-03-11")
    assert _linhas(filtrado, "periodo", "total") == [("2026-03-11", 2)]
    desc = await _get(client, contas["gestor"], "/relatorios/requisicoes-por-periodo", direcao="desc")
    assert desc["itens"][0]["periodo"] == "2026-03-12"


async def test_requisicoes_por_semana(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    t = await _get(client, contas["gestor"], "/relatorios/requisicoes-por-periodo", agrupar="semana")
    assert _linhas(t, "periodo", "total") == [("2026-03-09", 5)]       # semana começa na segunda-feira


# ---------------------------------------------------------------- 3) estoque crítico

async def test_estoque_critico(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    t = await _get(client, contas["almox"], "/relatorios/estoque-critico")
    assert _linhas(t, "material", "estoque", "critico", "falta_para_critico", "percentual_do_critico", "situacao") == [
        ("=SOMA(A1)", 0, 1, 1, 0, "Zerado"), ("Cabo", 3, 10, 7, 30, "Abaixo do crítico"), ("Fita", 1.5, 3, 1.5, 50, "Abaixo do crítico")]
    assert _linhas(await _get(client, contas["almox"], "/relatorios/estoque-critico", busca="fit"), "material") == [("Fita",)]
    assert [m for m in (await _get(client, contas["almox"], "/relatorios/estoque-critico", ordenar="falta", direcao="desc"))["itens"]][0]["material"] == "Cabo"


# ---------------------------------------------------------------- 4) taxa de atendimento

async def test_taxa_de_atendimento(client, contas, SessionTeste):
    ids = await _dados(contas, SessionTeste)
    t = await _get(client, contas["gestor"], "/relatorios/taxa-atendimento", ordenar="material", direcao="asc")
    r = t["resumo"]
    assert (r["total_resolvidas"], r["aprovadas"], r["parciais"], r["negadas"]) == (4, 2, 1, 1)   # a pendente não conta
    assert (r["pct_aprovadas"], r["pct_parciais"], r["pct_negadas"]) == (50.0, 25.0, 25.0)
    assert r["taxa_atendimento_quantidade_pct"] == 43.2                                              # 19 de 44
    cabo, disj = t["itens"]
    assert (cabo["material"], cabo["requisicoes"], cabo["aprovadas"], cabo["parciais"], cabo["negadas"]) == ("Cabo", 3, 1, 1, 1)
    assert (cabo["solicitado"], cabo["atendido"], cabo["deficit_acumulado"]) == (40, 15, 25)
    assert round(cabo["pct_aprovadas"], 1) == 33.3 and cabo["taxa_atendimento_qtd"] == 37.5
    assert (disj["requisicoes"], disj["deficit_acumulado"]) == (1, 0)
    so_a = await _get(client, contas["gestor"], "/relatorios/taxa-atendimento", obra_id=ids["A"])
    assert so_a["resumo"]["total_resolvidas"] == 2


# ---------------------------------------------------------------- 5) desempenho

async def test_desempenho_do_processamento(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    t = await _get(client, contas["gestor"], "/relatorios/desempenho", ordenar="worker", direcao="asc")
    r = t["resumo"]
    assert (r["processadas"], r["espera_media_s"], r["espera_max_s"]) == (4, 5.0, 8.0)
    assert r["espera_p50_s"] == 5.0 and abs(r["espera_p95_s"] - 7.7) < 0.01       # percentil contínuo de [2,4,6,8]
    w1, w2 = t["itens"]
    assert (w1["worker"], w1["processadas"], round(w1["espera_media_s"], 2), w1["espera_max_s"]) == ("worker-1", 3, 5.33, 8.0)
    assert (w2["worker"], w2["processadas"], w2["espera_media_s"]) == ("worker-2", 1, 4.0)


# ---------------------------------------------------------------- CSV

async def test_csv_compativel_com_excel(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    r = await client.get("/relatorios/consumo", headers=contas["gestor"], params={"formato": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv") and "charset=utf-8" in r.headers["content-type"]
    assert r.headers["content-disposition"] == 'attachment; filename="consumo.csv"'
    assert r.content.startswith(b"\xef\xbb\xbf")                                   # UTF-8 com BOM
    linhas = r.content.decode("utf-8-sig").splitlines()
    assert linhas[0] == "Obra;Material;Unidade;Quantidade atendida;Quantidade solicitada;Requisições"   # cabeçalho em português, ';'
    assert linhas[1] == "Obra A;Cabo;m;15;30;2"
    assert len(linhas) == 3


async def test_csv_usa_virgula_decimal_e_protege_contra_formula(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    r = await client.get("/relatorios/estoque-critico", headers=contas["gestor"], params={"formato": "csv"})
    linhas = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert linhas[0] == ["Material", "Unidade", "Estoque atual", "Estoque crítico", "Falta para o crítico", "% do crítico", "Situação"]
    por_nome = {l[0]: l for l in linhas[1:]}
    assert por_nome["Fita"] == ["Fita", "rolo", "1,5", "3", "1,5", "50,0", "Abaixo do crítico"]    # vírgula decimal
    assert "'=SOMA(A1)" in por_nome                                                                   # fórmula neutralizada


async def test_csv_ignora_paginacao_e_exporta_tudo(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    r = await client.get("/relatorios/requisicoes-por-periodo", headers=contas["gestor"], params={"formato": "csv", "tamanho": 1})
    linhas = r.content.decode("utf-8-sig").splitlines()
    assert linhas[0].startswith("Período;Total;Pendentes;Em processamento;Aprovadas") and len(linhas) == 4
    assert linhas[1].startswith("10/03/2026;1;")


async def test_todos_os_relatorios_exportam_csv(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    for rota in ("consumo", "requisicoes-por-periodo", "estoque-critico", "taxa-atendimento", "desempenho"):
        r = await client.get(f"/relatorios/{rota}", headers=contas["gestor"], params={"formato": "csv"})
        assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf"), rota
        assert r.headers["content-disposition"].endswith(f'{rota}.csv"'), rota


# ---------------------------------------------------------------- permissões

async def test_engenheiro_so_ve_a_propria_obra(client, contas, SessionTeste):
    ids = await _dados(contas, SessionTeste)
    h = contas["eng"]
    consumo = await _get(client, h, "/relatorios/consumo")
    assert _linhas(consumo, "obra", "quantidade_atendida") == [("Obra A", 15)]
    assert (await client.get("/relatorios/consumo", headers=h, params={"obra_id": ids["B"]})).status_code == 403
    assert (await _get(client, h, "/relatorios/taxa-atendimento"))["resumo"]["total_resolvidas"] == 2
    assert _linhas(await _get(client, h, "/relatorios/requisicoes-por-periodo"), "periodo", "total") == [
        ("2026-03-10", 1), ("2026-03-11", 1), ("2026-03-12", 1)]
    assert (await client.get("/relatorios/estoque-critico", headers=h)).status_code == 200
    assert (await client.get("/relatorios/desempenho", headers=h)).status_code == 403
    csv_ = await client.get("/relatorios/consumo", headers=h, params={"formato": "csv"})
    assert "Obra B" not in csv_.content.decode("utf-8-sig")


async def test_sem_token_da_401_nos_relatorios(client):
    for rota in ("consumo", "requisicoes-por-periodo", "estoque-critico", "taxa-atendimento", "desempenho"):
        assert (await client.get(f"/relatorios/{rota}")).status_code == 401, rota
    assert (await client.get("/dashboard/completo")).status_code == 401


# ---------------------------------------------------------------- dashboard completo

async def test_dashboard_completo_do_gestor(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    d = await _get(client, contas["gestor"], "/dashboard/completo", periodo="todos")
    c = d["cards"]
    assert c["total_materiais"] == 5 and c["materiais_alerta"] == 3          # + o "Cabo 10mm2" da fixture (100, crítico 10)
    assert c["requisicoes_abertas"] == 1 and c["taxa_atendimento_pct"] == 50.0
    assert c["materiais_em_deficit"] == 0                                    # a pendente (3 un) cabe nos 50 do Disjuntor
    assert c["requisicoes_incompletas_periodo"] == 2
    assert d["por_status"]["aprovada"] == 2 and d["por_status"]["pendente"] == 1
    assert [x["material"] for x in d["top_materiais"]] == ["Cabo", "Disjuntor"] and d["top_materiais"][0]["quantidade"] == 15
    assert {(o["obra"], o["itens_atendidos"]) for o in d["consumo_por_obra"]} == {("Obra A", 2), ("Obra B", 1)}
    assert [a["nome"] for a in d["alertas"]][:1] == ["=SOMA(A1)"] and len(d["alertas"]) == 3


async def test_dashboard_do_engenheiro_e_restrito(client, contas, SessionTeste):
    await _dados(contas, SessionTeste)
    d = await _get(client, contas["eng"], "/dashboard/completo", periodo="todos")
    assert d["cards"]["materiais_em_deficit"] is None                        # visão global: só gestor/almoxarife
    assert d["cards"]["fila_pendente"] is None
    assert d["cards"]["requisicoes_abertas"] == 1 and d["por_status"]["negada"] == 0
    assert d["consumo_por_obra"] == [{"obra": "Obra A", "itens_atendidos": 2}]


async def test_dashboard_calcula_deficit_e_filtra_periodo(client, contas, SessionTeste):
    ids = await _dados(contas, SessionTeste)
    agora = datetime.utcnow()
    await criar_requisicao(SessionTeste, ids["A"], ids["cabo"], 50, status=S.PENDENTE, criado_em=agora)   # 50 > 3 em estoque
    await criar_requisicao(SessionTeste, ids["A"], ids["disj"], 2, atendida=2, status=S.APROVADA, criado_em=agora - timedelta(days=1))
    await criar_requisicao(SessionTeste, ids["A"], ids["disj"], 2, atendida=2, status=S.APROVADA, criado_em=agora - timedelta(days=20))
    h = contas["gestor"]
    assert (await _get(client, h, "/dashboard/completo"))["cards"]["materiais_em_deficit"] == 1
    d7, d30, tudo = [await _get(client, h, "/dashboard/completo", periodo=p) for p in ("7", "30", "todos")]
    assert (d7["por_status"]["aprovada"], d30["por_status"]["aprovada"]) == (1, 2)                       # 1 dia atrás x 20 dias atrás
    assert (len(d7["por_dia"]), len(d30["por_dia"])) == (7, 30)                                          # dias sem pedido aparecem com 0
    assert sum(x["total"] for x in d7["por_dia"]) == 2 and tudo["de"] is None
    assert (await client.get("/dashboard/completo", headers=h, params={"periodo": "99"})).status_code == 422
