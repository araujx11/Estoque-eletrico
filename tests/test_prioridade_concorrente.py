"""
A prioridade vale na ALOCAÇÃO do estoque quando há vários workers? (ACHADO 14, docs/achados.md)

A fila RETIRA os itens em ordem de prioridade, mas cada worker segue sozinho para o banco: com N workers os itens correm juntos e o
primeiro a obter o lock do material nem sempre é o mais prioritário. Na vida real isso é uma CORRIDA (frequência medida entre 0 % e 35 %,
conforme conexões frias/quentes e tráfego). Para o teste ser determinístico, atrasamos a abertura da conexão do worker que pegou o item
mais prioritário (o que acontece de verdade com uma conexão fria ou lenta). Com 1 worker não há com quem competir: a ordem é estrita.
"""
import asyncio
from datetime import datetime, timedelta

import pytest

import app.services.fila_service as fila_service
from app.services.fila_service import FilaRequisicoes, worker_loop
from tests.helpers import criar_material, criar_obra, criar_requisicoes, esperar_processadas, requisicoes_de

T0 = datetime(2026, 1, 1, 12, 0, 0)


class _Aberta:
    def __init__(self, cm, atraso):
        self.cm, self.atraso = cm, atraso

    async def __aenter__(self):
        await asyncio.sleep(self.atraso)          # conexão lenta/fria
        return await self.cm.__aenter__()

    async def __aexit__(self, *a):
        return await self.cm.__aexit__(*a)


class _SessaoComPrimeiraLenta:
    """Delega ao AsyncSessionLocal real, atrasando só a PRIMEIRA abertura (a do worker que retirou o item mais prioritário)."""
    def __init__(self, real, atraso):
        self.real, self.atraso, self.n = real, atraso, 0

    def __call__(self):
        self.n += 1
        return _Aberta(self.real(), self.atraso if self.n == 1 else 0)


async def _quem_levou_o_estoque(Sessao, monkeypatch, n_workers: int):
    """Estoque para UM pedido (10 de 10); 3 obras pedem 10 cada. Retorna a prioridade (0 = a mais urgente) da obra que levou."""
    mid = await criar_material(Sessao, "Cabo", estoque=10)
    obras = [await criar_obra(Sessao, f"Obra {k}", prioridade=k + 1, parada=(k == 0)) for k in range(3)]
    ids = [(await criar_requisicoes(Sessao, o, mid, [10]))[0] for o in obras]
    fila = FilaRequisicoes()
    for k in reversed(range(3)):                   # chegam em ordem inversa à prioridade
        await fila.adicionar(ids[k], k == 0, k + 1, None, T0 + timedelta(seconds=k))
    monkeypatch.setattr(fila_service, "AsyncSessionLocal", _SessaoComPrimeiraLenta(fila_service.AsyncSessionLocal, 0.15))
    workers = [asyncio.create_task(worker_loop(f"w{j}", fila)) for j in range(n_workers)]
    try:
        await esperar_processadas(Sessao, ids)
    finally:
        for w in workers:
            w.cancel()
    por_id = {r.id: r for r in await requisicoes_de(Sessao, mid)}
    return next(k for k, rid in enumerate(ids) if por_id[rid].quantidade_atendida == 10)


async def test_com_um_worker_a_prioridade_e_estrita_mesmo_com_conexao_lenta(SessionTeste, monkeypatch):
    assert await _quem_levou_o_estoque(SessionTeste, monkeypatch, n_workers=1) == 0


@pytest.mark.xfail(strict=True, reason="ACHADO 14: com vários workers a ordem de retirada da fila não garante a ordem de alocação")
async def test_com_varios_workers_a_prioridade_vale_na_alocacao(SessionTeste, monkeypatch):
    # o worker que retirou a obra mais urgente demora para abrir a conexão; outro worker, com a obra de menor prioridade, chega ao lock antes
    assert await _quem_levou_o_estoque(SessionTeste, monkeypatch, n_workers=3) == 0
