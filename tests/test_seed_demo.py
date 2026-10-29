"""O seed de demonstração roda no banco de TESTE (nunca no de desenvolvimento) e precisa gerar dados coerentes."""
from argparse import Namespace

from sqlalchemy import func, select

from app.core.security import criar_token
from app.models import LogAuditoria, Material, MovimentacaoEstoque, Requisicao, StatusRequisicao as S, TipoMovimentacao as T, Usuario
from scripts import seed_demo


async def _contagens(Sessao):
    async with Sessao() as db:
        return {m.__tablename__: (await db.execute(select(func.count()).select_from(m))).scalar_one()
                for m in (Usuario, Material, Requisicao, MovimentacaoEstoque, LogAuditoria)}


async def test_seed_gera_dados_coerentes_e_e_idempotente(client, SessionTeste):
    await seed_demo.principal(Namespace(reset=False, sim=False, semente=2026))
    c1 = await _contagens(SessionTeste)
    assert c1["usuarios"] == 5 and c1["materiais"] >= 25 and c1["requisicoes"] > 150 and c1["auditoria"] > 10

    async with SessionTeste() as db:
        materiais = (await db.execute(select(Material))).scalars().all()
        movs = (await db.execute(select(MovimentacaoEstoque).order_by(MovimentacaoEstoque.id))).scalars().all()
        reqs = (await db.execute(select(Requisicao))).scalars().all()
        gestor = (await db.execute(select(Usuario).where(Usuario.email == "gestor@demo.local"))).scalar_one()

    # livro-razão fecha para cada material, na ordem dos ids: cada saldo_apos == saldo anterior ± quantidade
    por_material = {}
    for m in movs:
        por_material.setdefault(m.material_id, []).append(m)
    for mat in materiais:
        saldo = 0.0
        for m in por_material[mat.id]:
            saldo = saldo - m.quantidade if m.tipo == T.SAIDA else saldo + m.quantidade
            assert abs(m.saldo_apos - saldo) < 1e-6, f"{mat.nome}: movimentação {m.id}"
        assert abs(saldo - mat.quantidade_estoque) < 1e-6 and mat.quantidade_estoque >= 0, mat.nome

    # as saídas batem com o que as requisições dizem que atenderam
    assert sum(m.quantidade for m in movs if m.tipo == T.SAIDA) == sum(r.quantidade_atendida for r in reqs)
    status = {s: sum(1 for r in reqs if r.status == s) for s in S}
    assert status[S.APROVADA] > 0 and status[S.PARCIAL] > 0 and status[S.NEGADA] > 0   # histórico variado
    assert status[S.PENDENTE] >= 7                                                     # pedidos abertos para o diagnóstico

    # o diagnóstico mostra as três cores, incluindo o cenário do enunciado (déficit de 20 m)
    h = {"Authorization": f"Bearer {criar_token(gestor.id)}"}
    d = (await client.get("/diagnostico/deficit", headers=h)).json()
    cores = {m["semaforo"] for m in d["materiais"]}
    assert cores == {"verde", "amarelo", "vermelho"}
    cabo = next(m for m in d["materiais"] if m["nome"] == "Cabo 10 mm²")
    assert cabo["mensagem"] == "Cabo 10 mm² insuficiente. Estoque: 100 m. Demanda: 120 m. Déficit: 20 m"
    assert [l["obra_nome"] for l in cabo["requisicoes"]] == ["Galpão Logístico Olinda", "Edifício Atlântico", "Residencial Jaboatão Park"]
    assert [l["quantidade_atendida"] for l in cabo["requisicoes"]] == [50, 40, 10]

    # relatórios e dashboard ficam preenchidos
    dash = (await client.get("/dashboard/completo?periodo=30", headers=h)).json()
    assert sum(x["total"] for x in dash["por_dia"]) > 100 and len(dash["top_materiais"]) == 5 and dash["alertas"]
    assert (await client.get("/relatorios/taxa-atendimento", headers=h)).json()["resumo"]["total_resolvidas"] > 100

    # idempotente: rodar de novo não duplica nada
    await seed_demo.principal(Namespace(reset=False, sim=False, semente=2026))
    assert await _contagens(SessionTeste) == c1
