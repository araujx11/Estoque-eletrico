"""
Achado 2 (fila com vários processos) e achado 3 (reprocessamento de erros).

* "Processos" são simulados por instâncias independentes de FilaRequisicoes sobre o MESMO banco, cada uma com o seu
  identificador e os seus workers (como `uvicorn --workers N` faria, só que no mesmo event loop).
* Os erros são injetados trocando `fila_service.processar_requisicao` por um invólucro que falha de propósito.
"""
import asyncio
import random
from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select, text, update

from app.models import LogAuditoria, MovimentacaoEstoque, Requisicao, StatusRequisicao as S
from app.services import fila_service
from app.services.estoque_service import processar_requisicao as processar_real
from app.services.fila_service import (
    FilaRequisicoes, reenfileirar_pendentes, reprocessar_erros, reprocessar_erros_loop, worker_loop,
)
from tests.helpers import (
    criar_material, criar_obra, criar_requisicoes, esperar_processadas, estoque_de, requisicoes_de,
)


async def _esperar(condicao, timeout=15.0):
    """Espera uma corrotina-condição (sem argumentos) ficar verdadeira."""
    limite = asyncio.get_running_loop().time() + timeout
    while not await condicao():
        if asyncio.get_running_loop().time() > limite:
            raise TimeoutError("condição não atendida a tempo")
        await asyncio.sleep(0.02)


async def _requisicao(Sessao, rid):
    async with Sessao() as db:
        return await db.get(Requisicao, rid)


async def _esvaziar(fila: FilaRequisicoes) -> list[str]:
    ids = []
    while fila.tamanho():
        ids.append(await fila.proximo())
    return ids


def _iniciar_workers(fila, n=2, prefixo="w"):
    return [asyncio.create_task(worker_loop(f"{prefixo}{i}", fila)) for i in range(n)]


async def _parar(*tarefas):
    for t in tarefas:
        t.cancel()
    await asyncio.gather(*tarefas, return_exceptions=True)


@pytest.fixture(autouse=True)
def _config_rapida(monkeypatch):
    monkeypatch.setenv("ERRO_BACKOFF_BASE_S", "0")        # sem espera entre tentativas, salvo nos testes de backoff
    monkeypatch.setenv("ERRO_MAX_TENTATIVAS", "3")
    monkeypatch.setenv("FILA_LEASE_S", "300")


# ======================================================================== Parte A: vários processos

async def test_tres_instancias_reivindicam_conjuntos_disjuntos_e_completos(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=1000)
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [1] * 60)
    filas = [FilaRequisicoes() for _ in range(3)]

    await asyncio.gather(*(reenfileirar_pendentes(f, SessionTeste, f"proc-{i}") for i, f in enumerate(filas)))

    por_fila = [await _esvaziar(f) for f in filas]
    todos = [i for lista in por_fila for i in lista]
    assert sorted(todos) == sorted(ids), "toda requisição precisa estar em alguma fila..."
    assert len(todos) == len(set(todos)), "...e em UMA só (nenhuma duplicada entre processos)"
    reqs = await requisicoes_de(SessionTeste)
    assert {r.status for r in reqs} == {S.EM_PROCESSAMENTO}
    donos = {r.id: r.reivindicada_por for r in reqs}
    for i, lista in enumerate(por_fila):
        assert all(donos[rid] == f"proc-{i}" for rid in lista), "a reivindicação aponta para quem a enfileirou"


async def test_quem_ja_foi_reivindicado_nao_e_reivindicado_de_novo(SessionTeste):
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    await criar_requisicoes(SessionTeste, oid, mid, [1, 2, 3])
    a, b = FilaRequisicoes(), FilaRequisicoes()

    assert await reenfileirar_pendentes(a, SessionTeste, "A") == 3
    assert await reenfileirar_pendentes(b, SessionTeste, "B") == 0       # o prazo de A ainda vale
    assert a.tamanho() == 3 and b.tamanho() == 0


async def test_prazo_vencido_permite_outro_processo_retomar(SessionTeste):
    """Se o dono caiu (prazo vencido), outro processo assume; sem isso a requisição ficaria presa para sempre."""
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    await criar_requisicoes(SessionTeste, oid, mid, [1, 2])
    await reenfileirar_pendentes(FilaRequisicoes(), SessionTeste, "morto")
    async with SessionTeste() as db:                                      # envelhece a reivindicação além do prazo
        await db.execute(update(Requisicao).values(reivindicada_em=func.now() - timedelta(seconds=301)))
        await db.commit()

    nova = FilaRequisicoes()
    assert await reenfileirar_pendentes(nova, SessionTeste, "vivo") == 2
    assert nova.tamanho() == 2
    assert {r.reivindicada_por for r in await requisicoes_de(SessionTeste)} == {"vivo"}


async def test_skip_locked_nao_espera_linha_travada_por_outro(SessionTeste):
    """Uma requisição travada por outra transação é PULADA (a reivindicação não fica esperando) e fica para depois."""
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    travada, livre = await criar_requisicoes(SessionTeste, oid, mid, [1, 2])
    fila = FilaRequisicoes()

    async with SessionTeste() as outro, outro.begin():
        await outro.execute(select(Requisicao).where(Requisicao.id == travada).with_for_update())
        n = await asyncio.wait_for(reenfileirar_pendentes(fila, SessionTeste, "A"), timeout=5)   # não pode bloquear
        assert n == 1 and await _esvaziar(fila) == [livre]
    # lock liberado: a que ficou para trás ainda está pendente e é reivindicada na próxima passada
    assert (await _requisicao(SessionTeste, travada)).status == S.PENDENTE
    assert await reenfileirar_pendentes(fila, SessionTeste, "A") == 1


async def test_tres_instancias_processam_cada_requisicao_exatamente_uma_vez(SessionTeste):
    """Ponta a ponta: 3 'processos' (cada um com fila e workers próprios) esvaziam a mesma carga, com déficit."""
    rnd = random.Random(7)
    inicial = D("400")
    mats = [await criar_material(SessionTeste, nome=f"M{i}", estoque=inicial) for i in range(3)]
    obras = [await criar_obra(SessionTeste, nome=f"O{p}", prioridade=p) for p in (1, 3, 5)]
    ids = []
    for mid in mats:                                                      # demanda > estoque em cada material
        for oid in obras:
            ids += await criar_requisicoes(SessionTeste, oid, mid, [D(rnd.randint(500, 4000)) / 100 for _ in range(15)])
    filas = [FilaRequisicoes() for _ in range(3)]
    await asyncio.gather(*(reenfileirar_pendentes(f, SessionTeste, f"proc-{i}") for i, f in enumerate(filas)))
    assert sum(f.tamanho() for f in filas) == len(ids)

    workers = [t for i, f in enumerate(filas) for t in _iniciar_workers(f, 3, f"p{i}w")]
    try:
        await esperar_processadas(SessionTeste, ids, timeout=60)
    finally:
        await _parar(*workers)

    reqs = await requisicoes_de(SessionTeste)
    assert len(reqs) == len(ids) and {r.status for r in reqs} <= {S.APROVADA, S.PARCIAL, S.NEGADA}
    async with SessionTeste() as db:
        saidas = (await db.execute(
            select(MovimentacaoEstoque.requisicao_id, func.count()).group_by(MovimentacaoEstoque.requisicao_id))).all()
    assert all(n == 1 for _, n in saidas), "alguma requisição gerou mais de uma saída"
    assert {rid for rid, _ in saidas} == {r.id for r in reqs if r.quantidade_atendida > 0}
    for mid in mats:                                                      # conservação exata, material a material
        atendido = sum((r.quantidade_atendida for r in reqs if r.material_id == mid), D(0))
        final = await estoque_de(SessionTeste, mid)
        assert final >= 0 and atendido + final == inicial


async def test_prioridade_dentro_de_cada_instancia_e_global_quando_uma_so_reivindica(SessionTeste):
    """A fila de cada processo sai em ordem de prioridade. Com um único processo reivindicando (partida normal, em que
    ele pega tudo de uma vez), a prioridade vale para o conjunto inteiro: a obra urgente leva o estoque."""
    mid = await criar_material(SessionTeste, estoque=10)
    urgente = await criar_obra(SessionTeste, nome="Urgente", prioridade=1)
    calma = await criar_obra(SessionTeste, nome="Calma", prioridade=5)
    (r_calma,) = await criar_requisicoes(SessionTeste, calma, mid, [10])       # criada ANTES e menos urgente
    (r_urgente,) = await criar_requisicoes(SessionTeste, urgente, mid, [10])
    a, b, c = FilaRequisicoes(), FilaRequisicoes(), FilaRequisicoes()

    assert await reenfileirar_pendentes(a, SessionTeste, "A") == 2
    assert await reenfileirar_pendentes(b, SessionTeste, "B") == 0 and await reenfileirar_pendentes(c, SessionTeste, "C") == 0
    assert await _esvaziar(a) == [r_urgente, r_calma]

    await reenfileirar_pendentes(a, SessionTeste, "A2")                       # nada novo: já reivindicadas
    for rid in (r_urgente, r_calma):                                           # processa na ordem em que a fila entregou
        await _processar_real(SessionTeste, rid)
    assert (await _requisicao(SessionTeste, r_urgente)).status == S.APROVADA
    assert (await _requisicao(SessionTeste, r_calma)).status == S.NEGADA


async def _processar_real(Sessao, rid, worker="w"):
    async with Sessao() as db:
        return await processar_real(db, rid, worker)


# ======================================================================== Parte B: reprocessar erros

def _falhar(monkeypatch, quando):
    """Troca o processamento por um que falha conforme `quando(numero_da_chamada, rid)` (1ª chamada = 1)."""
    chamadas: dict[str, int] = {}

    async def falso(db, rid, worker, ao_travar_material=None):
        chamadas[rid] = chamadas.get(rid, 0) + 1
        if quando(chamadas[rid], rid) == "antes":
            raise ConnectionError("falha simulada antes do débito")
        resultado = await processar_real(db, rid, worker, ao_travar_material)
        if quando(chamadas[rid], rid) == "depois":
            raise ConnectionError("falha simulada depois do commit")
        return resultado

    monkeypatch.setattr(fila_service, "processar_requisicao", falso)
    return chamadas


async def _saidas(Sessao, rid):
    async with Sessao() as db:
        return (await db.execute(select(func.count()).select_from(MovimentacaoEstoque)
                                 .where(MovimentacaoEstoque.requisicao_id == rid))).scalar_one()


async def test_erro_transitorio_e_reprocessado_com_sucesso_e_debita_uma_vez(SessionTeste, monkeypatch):
    monkeypatch.setenv("ERRO_REPROCESSAR_INTERVALO_S", "0.05")
    chamadas = _falhar(monkeypatch, lambda n, rid: "antes" if n == 1 else None)       # só a 1ª tentativa falha
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])
    fila = FilaRequisicoes()
    await reenfileirar_pendentes(fila, SessionTeste, "A")
    tarefas = [*_iniciar_workers(fila, 2), asyncio.create_task(reprocessar_erros_loop(fila))]
    try:
        async def aprovada():
            return (await _requisicao(SessionTeste, rid)).status == S.APROVADA
        await _esperar(aprovada)                                  # a rotina periódica recolocou o erro na fila sozinha
    finally:
        await _parar(*tarefas)

    r = await _requisicao(SessionTeste, rid)
    assert chamadas[rid] == 2 and r.tentativas == 1 and "falha simulada" in r.ultimo_erro
    assert r.quantidade_atendida == 30 and await estoque_de(SessionTeste, mid) == 70, "debitou mais (ou menos) de uma vez"
    assert await _saidas(SessionTeste, rid) == 1


async def test_erro_persistente_para_no_limite_e_vira_erro_definitivo(SessionTeste, monkeypatch):
    chamadas = _falhar(monkeypatch, lambda n, rid: "antes")                           # sempre falha
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])
    fila = FilaRequisicoes()
    await reenfileirar_pendentes(fila, SessionTeste, "A")
    workers = _iniciar_workers(fila, 1)
    try:
        for tentativa in (1, 2, 3):
            async def com_n_falhas(n=tentativa):
                r = await _requisicao(SessionTeste, rid)
                return r.status == S.ERRO and r.tentativas == n
            await _esperar(com_n_falhas)
            if tentativa < 3:
                assert await reprocessar_erros(fila, SessionTeste, "A") == 1          # ainda tem direito a nova tentativa
        assert await reprocessar_erros(fila, SessionTeste, "A") == 0                  # limite atingido: não volta mais
        await asyncio.sleep(0.3)
    finally:
        await _parar(*workers)

    r = await _requisicao(SessionTeste, rid)
    assert r.status == S.ERRO and r.tentativas == 3 and chamadas[rid] == 3
    assert "ConnectionError" in r.ultimo_erro and r.proxima_tentativa_em is None
    assert await estoque_de(SessionTeste, mid) == 100 and await _saidas(SessionTeste, rid) == 0
    async with SessionTeste() as db:
        logs = (await db.execute(select(LogAuditoria).where(LogAuditoria.acao == "requisicao_erro_definitivo"))).scalars().all()
    assert len(logs) == 1 and logs[0].entidade_id == rid and logs[0].detalhes["tentativas"] == 3


async def test_diagnostico_mostra_erros_em_andamento_e_definitivos(client, gestor, dados, SessionTeste):
    obra, mat = dados["obra_a"]["id"], dados["material"]["id"]
    em_andamento, definitivo = await criar_requisicoes(SessionTeste, obra, mat, [5, 7])
    async with SessionTeste() as db:
        await db.execute(update(Requisicao).where(Requisicao.id == em_andamento).values(
            status=S.ERRO, tentativas=1, ultimo_erro="ConnectionError: x"))
        await db.execute(update(Requisicao).where(Requisicao.id == definitivo).values(
            status=S.ERRO, tentativas=3, ultimo_erro="ConnectionError: y"))
        await db.commit()

    r = await client.get("/diagnostico/deficit", headers=gestor)
    assert r.status_code == 200
    por_id = {e["requisicao_id"]: e for e in r.json()["erros"]}
    assert por_id[em_andamento]["definitivo"] is False and por_id[em_andamento]["tentativas"] == 1
    assert por_id[definitivo]["definitivo"] is True and por_id[definitivo]["max_tentativas"] == 3
    assert por_id[definitivo]["ultimo_erro"] == "ConnectionError: y" and por_id[definitivo]["quantidade_solicitada"] == 7


async def test_backoff_adia_a_proxima_tentativa(SessionTeste, monkeypatch):
    monkeypatch.setenv("ERRO_BACKOFF_BASE_S", "3600")
    _falhar(monkeypatch, lambda n, rid: "antes" if n == 1 else None)
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [10])
    fila = FilaRequisicoes()
    await reenfileirar_pendentes(fila, SessionTeste, "A")
    workers = _iniciar_workers(fila, 1)
    try:
        async def em_erro():
            return (await _requisicao(SessionTeste, rid)).status == S.ERRO
        await _esperar(em_erro)
        assert await reprocessar_erros(fila, SessionTeste, "A") == 0, "o backoff de 1 h ainda não venceu"
        async with SessionTeste() as db:                                  # vence o backoff
            await db.execute(update(Requisicao).values(proxima_tentativa_em=func.now() - timedelta(seconds=1)))
            await db.commit()
        assert await reprocessar_erros(fila, SessionTeste, "A") == 1

        async def aprovada():
            return (await _requisicao(SessionTeste, rid)).status == S.APROVADA
        await _esperar(aprovada)
    finally:
        await _parar(*workers)
    assert await estoque_de(SessionTeste, mid) == 90


async def test_falha_depois_do_commit_nao_vira_erro_nem_debita_de_novo(SessionTeste, monkeypatch):
    """A conexão cai DEPOIS do commit: o débito já existe. Marcar ERRO e reprocessar debitaria outra vez."""
    chamadas = _falhar(monkeypatch, lambda n, rid: "depois" if n == 1 else None)
    mid = await criar_material(SessionTeste, estoque=100)
    oid = await criar_obra(SessionTeste)
    (rid,) = await criar_requisicoes(SessionTeste, oid, mid, [30])
    fila = FilaRequisicoes()
    await reenfileirar_pendentes(fila, SessionTeste, "A")
    workers = _iniciar_workers(fila, 1)
    try:
        await esperar_processadas(SessionTeste, [rid])
        await asyncio.sleep(0.3)                                           # dá tempo de o `except` do worker rodar
        assert await reprocessar_erros(fila, SessionTeste, "A") == 0
        await asyncio.sleep(0.2)
    finally:
        await _parar(*workers)

    r = await _requisicao(SessionTeste, rid)
    assert r.status == S.APROVADA and r.tentativas == 0 and chamadas[rid] == 1
    assert await estoque_de(SessionTeste, mid) == 70 and await _saidas(SessionTeste, rid) == 1


async def test_varios_erros_transitorios_nenhuma_saida_duplicada(SessionTeste, monkeypatch):
    """Metade das requisições falha na 1ª vez; depois do reprocessamento cada uma tem no máximo uma saída."""
    _falhar(monkeypatch, lambda n, rid: "antes" if n == 1 and int(rid[:4], 16) % 2 == 0 else None)
    mid = await criar_material(SessionTeste, estoque=D("1000"))
    oid = await criar_obra(SessionTeste)
    ids = await criar_requisicoes(SessionTeste, oid, mid, [D("1.5")] * 40)
    fila = FilaRequisicoes()
    await reenfileirar_pendentes(fila, SessionTeste, "A")
    workers = _iniciar_workers(fila, 4)
    try:
        for _ in range(5):                                                 # algumas passadas da rotina periódica
            await asyncio.sleep(0.3)
            await reprocessar_erros(fila, SessionTeste, "A")
        await esperar_processadas(SessionTeste, ids, timeout=30)
    finally:
        await _parar(*workers)

    reqs = await requisicoes_de(SessionTeste)
    assert {r.status for r in reqs} == {S.APROVADA}
    assert any(r.tentativas == 1 for r in reqs), "o teste precisa ter provocado erros de verdade"
    assert await estoque_de(SessionTeste, mid) == D("1000") - D("1.5") * 40
    async with SessionTeste() as db:
        n = (await db.execute(select(func.count()).select_from(MovimentacaoEstoque))).scalar_one()
    assert n == 40
