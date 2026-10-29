"""Casos 5 e 6: ordem de atendimento pela fila de prioridade."""
import asyncio
import random
from datetime import datetime, timedelta

from app.models import StatusRequisicao as S
from app.services.fila_service import FilaRequisicoes, worker_loop
from tests.helpers import (
    criar_material, criar_obra, criar_requisicoes, esperar_processadas, estoque_de, requisicoes_de,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)


async def test_ordem_de_saida_da_fila_com_backlog():
    """Tudo é enfileirado ANTES de qualquer consumidor: a ordem de saída é a da prioridade."""
    # (id, parada, nivel, prazo, criado_em)
    itens = [
        ("parada_n2",           True,  2, T0 + timedelta(days=9), T0 + timedelta(minutes=5)),
        ("parada_n5",           True,  5, T0 + timedelta(days=1), T0),
        ("n1",                  False, 1, None,                   T0 + timedelta(minutes=9)),
        ("n3_prazo_5d",         False, 3, T0 + timedelta(days=5), T0 + timedelta(minutes=8)),
        ("n3_prazo_10d_antiga", False, 3, T0 + timedelta(days=10), T0 + timedelta(minutes=1)),
        ("n3_prazo_10d_nova",   False, 3, T0 + timedelta(days=10), T0 + timedelta(minutes=7)),
        ("n3_sem_prazo",        False, 3, None,                   T0),
        ("n5",                  False, 5, T0,                     T0),
    ]
    esperado = ["parada_n2", "parada_n5", "n1", "n3_prazo_5d",
                "n3_prazo_10d_antiga", "n3_prazo_10d_nova", "n3_sem_prazo", "n5"]

    for semente in range(5):                      # a ordem de chegada não pode influenciar o resultado
        embaralhado = itens[:]
        random.Random(semente).shuffle(embaralhado)
        fila = FilaRequisicoes()
        for rid, parada, nivel, prazo, criado in embaralhado:
            await fila.adicionar(rid, parada, nivel, prazo, criado)
        assert fila.tamanho() == len(itens)
        saida = [await asyncio.wait_for(fila.proximo(), 2) for _ in itens]
        assert saida == esperado, f"semente {semente}"


async def test_priorizacao_de_ponta_a_ponta_com_um_unico_worker(SessionTeste):
    """1 worker + backlog + estoque insuficiente: o déficit recai sobre as obras menos prioritárias."""
    mid = await criar_material(SessionTeste, "Cabo", estoque=120)
    obras = {
        "n5":     await criar_obra(SessionTeste, "n5", prioridade=5),
        "n3":     await criar_obra(SessionTeste, "n3", prioridade=3),
        "n1":     await criar_obra(SessionTeste, "n1", prioridade=1),
        "parada": await criar_obra(SessionTeste, "parada", prioridade=5, parada=True),
    }
    fila = FilaRequisicoes()
    ids = {}
    # chegam na ordem inversa da prioridade, cada obra pedindo 50
    for nome in ("n5", "n3", "n1", "parada"):
        (ids[nome],) = await criar_requisicoes(SessionTeste, obras[nome], mid, [50])
        await fila.adicionar(ids[nome], nome == "parada", {"n5": 5, "n3": 3, "n1": 1, "parada": 5}[nome], None,
                             T0 + timedelta(seconds=len(ids)))
    assert fila.tamanho() == 4                    # backlog formado antes de o worker existir

    worker = asyncio.create_task(worker_loop("worker-1", fila))
    try:
        await esperar_processadas(SessionTeste, list(ids.values()))
    finally:
        worker.cancel()

    por_id = {r.id: r for r in await requisicoes_de(SessionTeste, mid)}
    r = {nome: por_id[rid] for nome, rid in ids.items()}
    assert (r["parada"].status, r["parada"].quantidade_atendida) == (S.APROVADA, 50)
    assert (r["n1"].status, r["n1"].quantidade_atendida) == (S.APROVADA, 50)
    assert (r["n3"].status, r["n3"].quantidade_atendida) == (S.PARCIAL, 20)   # só sobrou 20
    assert (r["n5"].status, r["n5"].quantidade_atendida) == (S.NEGADA, 0)     # nada sobrou
    ordem = sorted(r, key=lambda n: r[n].processado_em)
    assert ordem == ["parada", "n1", "n3", "n5"]
    assert await estoque_de(SessionTeste, mid) == 0
