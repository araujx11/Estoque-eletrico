"""
A prioridade vale na ALOCAÇÃO do estoque quando há vários workers? (ACHADO 14, docs/achados.md; corrigido na Sprint 05)

A fila retira os itens em ordem de prioridade, mas, sozinha, isso não bastava: com N workers os itens correm juntos e o primeiro a
obter o lock do material nem sempre era o mais prioritário (uma CORRIDA, medida entre 0 % e 35 % conforme as condições). A correção são
as "senhas por material" (fila_service.Senha): cada item retirado entra numa fila do seu material, na ordem de retirada, e o worker só
abre a transação na sua vez. Para o teste ser determinístico, atrasamos a abertura da conexão do worker que pegou o item mais prioritário
(o que acontece de verdade com uma conexão fria ou lenta); o segundo teste usa latências aleatórias, com semente fixa.
"""
import asyncio
import random
from datetime import datetime, timedelta

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


class _Jitter:
    """Toda abertura de conexão ganha uma latência aleatória (0 a 15 ms), como numa conexão fria ou lenta."""
    def __init__(self, real, rng):
        self.real, self.rng = real, rng

    def __call__(self):
        return _Aberta(self.real(), self.rng.uniform(0.0, 0.015))


async def _quem_levou_o_estoque(Sessao, monkeypatch, n_workers: int, nome="Cabo", sessao=None):
    """Estoque para UM pedido (10 de 10); 3 obras pedem 10 cada. Retorna a prioridade (0 = a mais urgente) da obra que levou."""
    mid = await criar_material(Sessao, nome, estoque=10)
    obras = [await criar_obra(Sessao, f"Obra {k}", prioridade=k + 1, parada=(k == 0)) for k in range(3)]
    ids = [(await criar_requisicoes(Sessao, o, mid, [10]))[0] for o in obras]
    fila = FilaRequisicoes()
    for k in reversed(range(3)):                   # chegam em ordem inversa à prioridade
        await fila.adicionar(ids[k], k == 0, k + 1, None, T0 + timedelta(seconds=k), material_id=mid)
    monkeypatch.setattr(fila_service, "AsyncSessionLocal", sessao or _SessaoComPrimeiraLenta(fila_service.AsyncSessionLocal, 0.15))
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


async def test_com_varios_workers_a_prioridade_vale_na_alocacao(SessionTeste, monkeypatch):
    # o worker que retirou a obra mais urgente demora para abrir a conexão; outro worker, com a obra de menor prioridade, chega ao lock antes
    assert await _quem_levou_o_estoque(SessionTeste, monkeypatch, n_workers=3) == 0


async def test_prioridade_vale_com_latencia_de_conexao_aleatoria(SessionTeste, monkeypatch):
    """25 rodadas com 3 workers e latências aleatórias: a obra mais urgente tem de levar o estoque em TODAS (antes da correção, ~1 em cada 3)."""
    real = fila_service.AsyncSessionLocal
    vencedores = [await _quem_levou_o_estoque(SessionTeste, monkeypatch, n_workers=3, nome=f"Cabo {i}",
                                              sessao=_Jitter(real, random.Random(1000 + i))) for i in range(25)]
    assert vencedores == [0] * 25, f"inversões de prioridade: {[v for v in vencedores if v != 0]}"
