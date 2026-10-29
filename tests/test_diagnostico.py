"""Diagnóstico de déficit: simulação da distribuição do estoque atual entre a demanda aberta."""
import asyncio
import random
from datetime import datetime, timedelta

import pytest

from app.models import Obra, Requisicao, StatusRequisicao as S
from app.services.fila_service import FilaRequisicoes
from sqlalchemy import func, select
from tests.helpers import criar_material, criar_obra, criar_requisicao, estoque_de

T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _diag(client, headers, **params):
    r = await client.get("/diagnostico/deficit", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _mat(d, mid):
    return next(m for m in d["materiais"] if m["material_id"] == mid)


# ---------------------------------------------------------------- o exemplo do enunciado

async def test_cenario_do_enunciado_100m_com_40_30_50(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Cabo 10 mm²", estoque=100, critico=20, unidade="m")
    obra_c = await criar_obra(SessionTeste, "Obra C", prioridade=1, parada=True)   # a mais urgente: pede 50
    obra_a = await criar_obra(SessionTeste, "Obra A", prioridade=2)                # pede 40
    obra_b = await criar_obra(SessionTeste, "Obra B", prioridade=3)                # pede 30
    # chegam na ordem INVERSA da prioridade, para provar que a ordem vem da regra e não da chegada
    await criar_requisicao(SessionTeste, obra_b, mid, 30, criado_em=T0)
    await criar_requisicao(SessionTeste, obra_a, mid, 40, criado_em=T0 + timedelta(minutes=1))
    await criar_requisicao(SessionTeste, obra_c, mid, 50, criado_em=T0 + timedelta(minutes=2))

    m = _mat(await _diag(client, contas["gestor"]), mid)

    assert m["semaforo"] == "vermelho" and m["icone"] == "🔴"
    assert (m["estoque_atual"], m["demanda_total"], m["deficit"], m["estoque_restante"]) == (100, 120, 20, 0)
    assert m["mensagem"] == "Cabo 10 mm² insuficiente. Estoque: 100 m. Demanda: 120 m. Déficit: 20 m"
    linhas = m["requisicoes"]
    assert [l["obra_nome"] for l in linhas] == ["Obra C", "Obra A", "Obra B"]
    assert [l["ordem"] for l in linhas] == [1, 2, 3]
    assert [l["quantidade_pedida"] for l in linhas] == [50, 40, 30]
    assert [l["quantidade_atendida"] for l in linhas] == [50, 40, 10]
    assert [l["quantidade_pendente"] for l in linhas] == [0, 0, 20]
    assert [l["status"] for l in linhas] == ["completa", "completa", "parcial"]
    assert [l["icone"] for l in linhas] == ["🟢", "🟢", "🟡"]
    assert linhas[0]["obra_parada"] is True and not any(l["hipotetica"] for l in linhas)


async def test_ordem_do_diagnostico_identica_a_ordem_da_fila(client, contas, SessionTeste):
    """Para as MESMAS requisições, o diagnóstico percorre a demanda na mesma ordem em que a fila as entregaria."""
    rng = random.Random(11)
    mid = await criar_material(SessionTeste, "Material X", estoque=500)
    obras = [await criar_obra(SessionTeste, f"Obra {i}", prioridade=rng.randint(1, 5), parada=rng.random() < 0.3,
                              prazo=rng.choice([None, T0 + timedelta(days=rng.randint(1, 60))])) for i in range(6)]
    for i in range(25):
        await criar_requisicao(SessionTeste, rng.choice(obras), mid, rng.randint(1, 20),
                               criado_em=T0 + timedelta(minutes=rng.randint(0, 600)))

    # ordem real da fila
    async with SessionTeste() as db:
        linhas = (await db.execute(select(Requisicao, Obra).join(Obra, Obra.id == Requisicao.obra_id)
                                   .order_by(Requisicao.criado_em, Requisicao.id))).all()
    fila = FilaRequisicoes()
    for req, obra in linhas:
        await fila.adicionar(req.id, obra.parada_por_falta_material, obra.nivel_prioridade, obra.prazo_entrega, req.criado_em)
    ordem_fila = [await asyncio.wait_for(fila.proximo(), 2) for _ in linhas]

    ordem_diag = [l["requisicao_id"] for l in _mat(await _diag(client, contas["gestor"]), mid)["requisicoes"]]
    assert ordem_diag == ordem_fila and len(ordem_diag) == 25


# ---------------------------------------------------------------- semáforo

async def test_verde_estoque_cobre_e_sobra_acima_do_critico(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Disjuntor 20 A", estoque=100, critico=10, unidade="un")
    obra = await criar_obra(SessionTeste)
    await criar_requisicao(SessionTeste, obra, mid, 30)
    m = _mat(await _diag(client, contas["gestor"]), mid)
    assert (m["semaforo"], m["icone"], m["deficit"], m["estoque_restante"]) == ("verde", "🟢", 0, 70)
    assert "cobre a demanda (30 un)" in m["mensagem"] and "acima do crítico" in m["mensagem"]
    assert m["requisicoes"][0]["status"] == "completa"


async def test_amarelo_cobre_mas_o_restante_fica_abaixo_do_critico(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Eletroduto", estoque=100, critico=80, unidade="barra")
    obra = await criar_obra(SessionTeste)
    await criar_requisicao(SessionTeste, obra, mid, 30)          # restaria 70 (< 80)
    m = _mat(await _diag(client, contas["gestor"]), mid)
    assert (m["semaforo"], m["icone"], m["deficit"], m["estoque_restante"]) == ("amarelo", "🟡", 0, 70)
    assert "ficaria no crítico (80 barra) ou abaixo" in m["mensagem"]
    assert m["requisicoes"][0]["status"] == "completa"           # todos seriam atendidos; o alerta é só de reposição


async def test_amarelo_quando_o_restante_cai_exatamente_no_critico(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Tomada", estoque=100, critico=70)
    obra = await criar_obra(SessionTeste)
    await criar_requisicao(SessionTeste, obra, mid, 30)          # restante == crítico (mesma regra "<=" do alerta)
    assert _mat(await _diag(client, contas["gestor"]), mid)["semaforo"] == "amarelo"


async def test_vermelho_com_estoque_zerado(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Quadro", estoque=0, critico=2, unidade="un")
    obra = await criar_obra(SessionTeste)
    await criar_requisicao(SessionTeste, obra, mid, 10)
    m = _mat(await _diag(client, contas["gestor"]), mid)
    assert (m["semaforo"], m["deficit"], m["demanda_total"]) == ("vermelho", 10, 10)
    assert m["mensagem"] == "Quadro insuficiente. Estoque: 0 un. Demanda: 10 un. Déficit: 10 un"
    (l,) = m["requisicoes"]
    assert (l["status"], l["icone"], l["quantidade_atendida"], l["quantidade_pendente"]) == ("sem_atendimento", "🔴", 0, 10)


async def test_material_sem_demanda(client, contas, SessionTeste):
    folgado = await criar_material(SessionTeste, "Folgado", estoque=50, critico=10)
    no_limite = await criar_material(SessionTeste, "No limite", estoque=10, critico=10)
    baixo = await criar_material(SessionTeste, "Baixo", estoque=5, critico=10)
    d = await _diag(client, contas["gestor"])
    assert (_mat(d, folgado)["semaforo"], _mat(d, folgado)["demanda_total"], _mat(d, folgado)["requisicoes"]) == ("verde", 0, [])
    assert _mat(d, no_limite)["semaforo"] == "amarelo" and _mat(d, baixo)["semaforo"] == "amarelo"
    assert "sem demanda aberta" in _mat(d, baixo)["mensagem"]


async def test_materiais_vem_ordenados_por_gravidade(client, contas, SessionTeste):
    obra = await criar_obra(SessionTeste)
    verde = await criar_material(SessionTeste, "A verde", estoque=100, critico=1)
    vermelho = await criar_material(SessionTeste, "Z vermelho", estoque=1, critico=1)
    await criar_requisicao(SessionTeste, obra, vermelho, 5)
    amarelo = await criar_material(SessionTeste, "M amarelo", estoque=3, critico=3)
    ordem = [m["material_id"] for m in (await _diag(client, contas["gestor"]))["materiais"]]
    assert ordem.index(vermelho) < ordem.index(amarelo) < ordem.index(verde)


# ---------------------------------------------------------------- o que conta como demanda

async def test_so_pendente_e_em_processamento_contam_como_demanda(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Cabo", estoque=100, critico=0)
    obra = await criar_obra(SessionTeste)
    await criar_requisicao(SessionTeste, obra, mid, 10, status=S.PENDENTE)
    await criar_requisicao(SessionTeste, obra, mid, 20, status=S.EM_PROCESSAMENTO)
    for st in (S.APROVADA, S.PARCIAL, S.NEGADA, S.ERRO):
        await criar_requisicao(SessionTeste, obra, mid, 99, status=st, atendida=0)
    m = _mat(await _diag(client, contas["gestor"]), mid)
    assert m["demanda_total"] == 30 and len(m["requisicoes"]) == 2


async def test_deficit_historico_de_parciais_e_negadas(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Cabo", estoque=100)
    obra = await criar_obra(SessionTeste)
    await criar_requisicao(SessionTeste, obra, mid, 30, status=S.PARCIAL, atendida=10)    # faltou 20
    await criar_requisicao(SessionTeste, obra, mid, 20, status=S.NEGADA, atendida=0)      # faltou 20
    await criar_requisicao(SessionTeste, obra, mid, 15, status=S.APROVADA, atendida=15)   # não entra
    d = await _diag(client, contas["gestor"])
    (h,) = [x for x in d["deficit_historico"] if x["material_id"] == mid]
    assert (h["requisicoes_parciais"], h["requisicoes_negadas"], h["deficit_acumulado"]) == (1, 1, 40)


async def test_filtro_por_material_e_material_inexistente(client, contas, SessionTeste):
    a = await criar_material(SessionTeste, "A", estoque=10)
    await criar_material(SessionTeste, "B", estoque=10)
    d = await _diag(client, contas["gestor"], material_id=a)
    assert [m["material_id"] for m in d["materiais"]] == [a]
    assert (await client.get("/diagnostico/deficit?material_id=99999", headers=contas["gestor"])).status_code == 404


async def test_diagnostico_nao_altera_nada(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Cabo", estoque=100)
    obra = await criar_obra(SessionTeste)
    await criar_requisicao(SessionTeste, obra, mid, 40)
    for _ in range(3):
        await _diag(client, contas["gestor"])
    assert await estoque_de(SessionTeste, mid) == 100
    async with SessionTeste() as db:
        r = (await db.execute(select(Requisicao).where(Requisicao.material_id == mid))).scalars().all()
    assert len(r) == 1 and r[0].status == S.PENDENTE and r[0].quantidade_atendida == 0


# ---------------------------------------------------------------- "e se...?"

async def test_simulacao_hipotetica_respeita_prioridade_e_nao_grava(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Cabo", estoque=100, critico=0, unidade="m")
    comum = await criar_obra(SessionTeste, "Obra comum", prioridade=3)
    urgente = await criar_obra(SessionTeste, "Obra urgente", prioridade=1)
    await criar_requisicao(SessionTeste, comum, mid, 60, criado_em=T0)

    r = await client.post("/diagnostico/simular", headers=contas["gestor"], json={
        "requisicoes": [{"obra_id": urgente, "material_id": mid, "quantidade": 80}], "material_id": mid})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["simulacao_hipotetica"] is True
    m = _mat(d, mid)
    assert (m["demanda_total"], m["deficit"], m["semaforo"]) == (140, 40, "vermelho")
    l1, l2 = m["requisicoes"]
    assert (l1["obra_nome"], l1["hipotetica"], l1["quantidade_atendida"]) == ("Obra urgente", True, 80)   # passa na frente
    assert (l2["obra_nome"], l2["hipotetica"], l2["quantidade_atendida"], l2["quantidade_pendente"]) == ("Obra comum", False, 20, 40)

    async with SessionTeste() as db:   # nada foi gravado
        assert (await db.execute(select(func.count()).select_from(Requisicao))).scalar_one() == 1
    assert await estoque_de(SessionTeste, mid) == 100
    assert (await _diag(client, contas["gestor"]))["simulacao_hipotetica"] is False


async def test_hipotetica_com_mesma_prioridade_fica_por_ultimo(client, contas, SessionTeste):
    mid = await criar_material(SessionTeste, "Cabo", estoque=100)
    obra = await criar_obra(SessionTeste, prioridade=3)
    await criar_requisicao(SessionTeste, obra, mid, 60, criado_em=T0)
    d = (await client.post("/diagnostico/simular", headers=contas["gestor"], json={
        "requisicoes": [{"obra_id": obra, "material_id": mid, "quantidade": 80}]})).json()
    l1, l2 = _mat(d, mid)["requisicoes"]
    assert (l1["hipotetica"], l1["quantidade_atendida"]) == (False, 60) and (l2["hipotetica"], l2["quantidade_atendida"]) == (True, 40)


@pytest.mark.parametrize("corpo,codigo", [
    ({"requisicoes": []}, 422),
    ({"requisicoes": [{"obra_id": 1, "material_id": 1, "quantidade": 0}]}, 422),
    ({"requisicoes": [{"obra_id": 99999, "material_id": 1, "quantidade": 5}]}, 404),
])
async def test_simulacao_invalida(client, contas, corpo, codigo):
    if codigo == 404:
        corpo["requisicoes"][0]["material_id"] = contas["material"]["id"]
    assert (await client.post("/diagnostico/simular", headers=contas["gestor"], json=corpo)).status_code == codigo


# ---------------------------------------------------------------- consistência da leitura

async def test_diagnostico_le_tudo_do_mesmo_instante(SessionTeste):
    """As várias consultas do diagnóstico rodam em REPEATABLE READ, mesmo quando a sessão já tinha uma transação
    aberta (a autenticação faz isso na API). Sem isso, um pedido processado no meio deixaria a simulação incoerente."""
    from sqlalchemy import text

    from app.services.diagnostico_service import diagnosticar
    async with SessionTeste() as db:
        await db.execute(select(1))                            # abre uma transação READ COMMITTED, como a autenticação
        assert (await db.execute(text("show transaction_isolation"))).scalar() == "read committed"
        await diagnosticar(db)
        assert (await db.execute(text("show transaction_isolation"))).scalar() == "repeatable read"


# ---------------------------------------------------------------- permissões

async def test_permissoes_do_diagnostico(client, contas):
    corpo = {"requisicoes": [{"obra_id": contas["obra_a"]["id"], "material_id": contas["material"]["id"], "quantidade": 5}]}
    assert (await client.get("/diagnostico/deficit")).status_code == 401
    assert (await client.post("/diagnostico/simular", json=corpo)).status_code == 401
    assert (await client.get("/diagnostico/deficit", headers=contas["eng"])).status_code == 403
    assert (await client.post("/diagnostico/simular", headers=contas["eng"], json=corpo)).status_code == 403
    assert (await client.get("/diagnostico/deficit", headers=contas["almox"])).status_code == 200
    assert (await client.post("/diagnostico/simular", headers=contas["almox"], json=corpo)).status_code == 200
