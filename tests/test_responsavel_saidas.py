"""RF16: a saída gerada ao processar uma requisição registra o solicitante como responsável."""
import importlib.util
from pathlib import Path

from sqlalchemy import select, text

from app.models import MovimentacaoEstoque, Perfil, Requisicao, TipoMovimentacao as T, Usuario
from tests.helpers import criar_material, criar_obra, movimentacoes_de, processar


async def _usuario(Sessao, nome="Eng Zé"):
    async with Sessao() as db:
        u = Usuario(nome=nome, email=f"{nome[:3].lower()}@t.com", senha_hash="x", perfil=Perfil.ENGENHEIRO)
        db.add(u)
        await db.commit()
        return u.id


async def _requisicao(Sessao, obra, mat, qtd, solicitante):
    async with Sessao() as db:
        r = Requisicao(obra_id=obra, material_id=mat, quantidade_solicitada=qtd, solicitante_id=solicitante)
        db.add(r)
        await db.commit()
        return r.id


async def test_saida_de_requisicao_com_solicitante_tem_usuario_id(SessionTeste):
    mid, oid, uid = await criar_material(SessionTeste, estoque=100), await criar_obra(SessionTeste), await _usuario(SessionTeste)
    rids = [await _requisicao(SessionTeste, oid, mid, q, uid) for q in (10, 20)]
    for rid in rids:
        await processar(SessionTeste, rid)
    movs = [m for m in await movimentacoes_de(SessionTeste, mid) if m.tipo == T.SAIDA]
    assert len(movs) == 2 and all(m.usuario_id == uid for m in movs)


async def test_solicitante_nulo_continua_nulo_sem_erro(SessionTeste):
    mid, oid = await criar_material(SessionTeste, estoque=100), await criar_obra(SessionTeste)
    rid = await _requisicao(SessionTeste, oid, mid, 5, None)
    await processar(SessionTeste, rid)
    (mov,) = await movimentacoes_de(SessionTeste, mid)
    assert mov.usuario_id is None


async def test_reprocessar_nao_duplica_a_saida_nem_muda_o_responsavel(SessionTeste):
    mid, oid, uid = await criar_material(SessionTeste, estoque=100), await criar_obra(SessionTeste), await _usuario(SessionTeste)
    rid = await _requisicao(SessionTeste, oid, mid, 30, uid)
    for _ in range(3):
        await processar(SessionTeste, rid)
    movs = await movimentacoes_de(SessionTeste, mid)
    assert len(movs) == 1 and movs[0].usuario_id == uid


async def test_historico_da_api_devolve_o_nome_do_responsavel(client, gestor, dados, SessionTeste):
    uid = await _usuario(SessionTeste, "Maria Eng")
    rid = await _requisicao(SessionTeste, dados["obra_a"]["id"], dados["material"]["id"], 10, uid)
    await processar(SessionTeste, rid)
    itens = (await client.get("/movimentacoes?tipo=saida", headers=gestor)).json()["itens"]
    assert [i["usuario_nome"] for i in itens] == ["Maria Eng"]


async def test_migracao_preenche_saidas_antigas(SessionTeste):
    """Saídas antigas (sem responsável) recebem o solicitante da requisição; sem solicitante continuam nulas."""
    spec = importlib.util.spec_from_file_location(
        "m0004", Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0004_responsavel_nas_saidas.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mid, oid, uid = await criar_material(SessionTeste, estoque=100), await criar_obra(SessionTeste), await _usuario(SessionTeste)
    com = await _requisicao(SessionTeste, oid, mid, 10, uid)
    sem = await _requisicao(SessionTeste, oid, mid, 10, None)
    for rid in (com, sem):
        await processar(SessionTeste, rid)
    async with SessionTeste() as db:
        await db.execute(text("UPDATE movimentacoes_estoque SET usuario_id = NULL"))      # simula o histórico antigo
        await db.execute(text(mod.PREENCHER))
        await db.commit()
        movs = {m.requisicao_id: m.usuario_id for m in (await db.execute(select(MovimentacaoEstoque))).scalars()}
    assert movs == {com: uid, sem: None}
