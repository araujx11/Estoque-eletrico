"""
Senhas por material da fila (ACHADO 14, corrigido na Sprint 05).

Cada item retirado da fila recebe uma Senha na fila do SEU material, na ordem de retirada (= prioridade). O worker só abre a
transação quando o item anterior do mesmo material terminou; materiais diferentes continuam em paralelo; e a vez é sempre liberada,
mesmo quando o processamento falha. Estes testes não usam o banco, exceto o do worker_loop (que abre uma sessão, sem consultar nada).
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import app.services.fila_service as fila_service
from app.services.fila_service import FilaRequisicoes, worker_loop

T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _fila(itens):
    """itens: [(id, nível de prioridade, material_id)] — chegam nessa ordem; a prioridade decide a saída."""
    fila = FilaRequisicoes()
    for i, (rid, nivel, mat) in enumerate(itens):
        await fila.adicionar(rid, False, nivel, None, T0 + timedelta(seconds=i), material_id=mat)
    return fila


async def _espera_bloqueada(senha, segundos=0.05) -> bool:
    """True se `senha.aguardar()` continua bloqueado depois de um instante."""
    tarefa = asyncio.create_task(senha.aguardar())
    await asyncio.sleep(segundos)
    bloqueada = not tarefa.done()
    tarefa.cancel()
    await asyncio.gather(tarefa, return_exceptions=True)
    return bloqueada


async def test_senhas_do_mesmo_material_seguem_a_prioridade_e_cada_uma_espera_a_anterior():
    fila = await _fila([("b", 3, 1), ("a", 1, 1), ("c", 2, 1)])               # ordem de prioridade: a, c, b
    (ra, sa), (rc, sc), (rb, sb) = [await fila.proximo_com_senha() for _ in range(3)]
    assert (ra, rc, rb) == ("a", "c", "b")

    await asyncio.wait_for(sa.aguardar(), 1)                                   # o primeiro não espera ninguém
    assert await _espera_bloqueada(sc) and await _espera_bloqueada(sb)         # os outros esperam

    sa.liberar()
    await asyncio.wait_for(sc.aguardar(), 1)                                   # agora c pode
    assert await _espera_bloqueada(sb)                                         # b ainda espera c
    sc.liberar()
    await asyncio.wait_for(sb.aguardar(), 1)
    sb.liberar()
    assert fila._ultima == {}, "a fila de senhas não pode acumular entradas depois que todos terminam"


async def test_materiais_diferentes_nao_se_bloqueiam():
    fila = await _fila([("a", 1, 1), ("b", 2, 2)])
    (_, sa), (_, sb) = [await fila.proximo_com_senha() for _ in range(2)]
    await asyncio.wait_for(sa.aguardar(), 1)
    await asyncio.wait_for(sb.aguardar(), 1)                                   # nenhum dos dois espera o outro
    sa.liberar()
    sb.liberar()
    assert fila._ultima == {}


async def test_item_sem_material_nao_bloqueia_nem_e_bloqueado():
    fila = FilaRequisicoes()
    await fila.adicionar("x", False, 1, None, T0)
    await fila.adicionar("y", False, 2, None, T0)
    (_, sx), (_, sy) = [await fila.proximo_com_senha() for _ in range(2)]
    await asyncio.wait_for(sx.aguardar(), 1)
    await asyncio.wait_for(sy.aguardar(), 1)
    sx.liberar()
    sy.liberar()
    assert fila._ultima == {}


async def test_liberar_duas_vezes_e_inofensivo():
    fila = await _fila([("a", 1, 1), ("b", 2, 1)])
    (_, sa), (_, sb) = [await fila.proximo_com_senha() for _ in range(2)]
    sa.liberar()
    sa.liberar()
    await asyncio.wait_for(sb.aguardar(), 1)
    sb.liberar()
    assert fila._ultima == {}


async def test_proximo_antigo_continua_devolvendo_so_o_id():
    fila = await _fila([("b", 2, 1), ("a", 1, 1)])
    assert [await fila.proximo(), await fila.proximo()] == ["a", "b"]
    assert fila._ultima == {}, "proximo() não emite senhas"


async def test_falha_no_processamento_libera_a_vez_do_proximo(monkeypatch):
    """Se o item da frente der erro, o próximo do mesmo material NÃO pode ficar esperando para sempre."""
    ordem, erros = [], []

    async def processar_falso(db, rid, worker, ao_travar_material=None):
        ordem.append(rid)
        if rid == "a":
            raise RuntimeError("falha simulada")
        return SimpleNamespace(status="aprovada", quantidade_atendida=1)

    async def marcar_erro_falso(rid, worker):
        erros.append(rid)

    monkeypatch.setattr(fila_service, "processar_requisicao", processar_falso)
    monkeypatch.setattr(fila_service, "_marcar_erro", marcar_erro_falso)
    fila = await _fila([("a", 1, 1), ("b", 2, 1), ("c", 3, 1)])
    workers = [asyncio.create_task(worker_loop(f"w{i}", fila)) for i in range(3)]
    try:
        for _ in range(100):
            if len(ordem) == 3:
                break
            await asyncio.sleep(0.02)
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    assert ordem == ["a", "b", "c"] and erros == ["a"]
    assert fila._ultima == {}


async def test_a_vez_do_proximo_e_liberada_ao_travar_o_material_sem_esperar_o_commit(monkeypatch):
    """Versão 2 da correção: o próximo item do material começa a se preparar assim que o anterior TRAVA o material,
    mesmo que o anterior ainda não tenha terminado (commit pendente). A ordem continua estrita, e a preparação do próximo
    (conexão, BEGIN, leitura da requisição) se sobrepõe ao fim do anterior."""
    comecaram, terminar_a = [], asyncio.Event()

    async def processar_falso(db, rid, worker, ao_travar_material=None):
        comecaram.append(rid)
        if ao_travar_material:
            ao_travar_material()                   # "já tenho o lock do material"
        if rid == "a":
            await terminar_a.wait()                # a ainda não deu commit
        return SimpleNamespace(status="aprovada", quantidade_atendida=1)

    monkeypatch.setattr(fila_service, "processar_requisicao", processar_falso)
    fila = await _fila([("a", 1, 1), ("b", 2, 1)])
    workers = [asyncio.create_task(worker_loop(f"w{i}", fila)) for i in range(2)]
    try:
        for _ in range(100):
            if comecaram == ["a", "b"]:
                break
            await asyncio.sleep(0.02)
        assert comecaram == ["a", "b"], "b deveria ter começado enquanto a ainda não terminou"
    finally:
        terminar_a.set()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


async def test_sem_travar_o_material_o_proximo_continua_esperando(monkeypatch):
    """Se o processamento NÃO chama o gancho (ex.: ainda aguardando o lock), o próximo do mesmo material espera: a ordem é estrita."""
    comecaram, terminar_a = [], asyncio.Event()

    async def processar_falso(db, rid, worker, ao_travar_material=None):
        comecaram.append(rid)
        if rid == "a":
            await terminar_a.wait()                # a não travou nada ainda
        if ao_travar_material:
            ao_travar_material()
        return SimpleNamespace(status="aprovada", quantidade_atendida=1)

    monkeypatch.setattr(fila_service, "processar_requisicao", processar_falso)
    fila = await _fila([("a", 1, 1), ("b", 2, 1)])
    workers = [asyncio.create_task(worker_loop(f"w{i}", fila)) for i in range(2)]
    try:
        await asyncio.sleep(0.2)
        assert comecaram == ["a"], "b não pode começar antes de a travar o material"
        terminar_a.set()
        for _ in range(100):
            if comecaram == ["a", "b"]:
                break
            await asyncio.sleep(0.02)
        assert comecaram == ["a", "b"]
    finally:
        terminar_a.set()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
