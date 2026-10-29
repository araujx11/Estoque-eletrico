"""A extração da chave de prioridade para app/services/prioridade.py é uma refatoração PURA:
a ordem tem de ser idêntica à implementação original que ficava dentro de FilaRequisicoes.adicionar."""
import asyncio
import random
from datetime import datetime, timedelta, timezone

from app.services.fila_service import FilaRequisicoes
from app.services.prioridade import chave_prioridade


def chave_original(parada, nivel, prazo, criado_em):
    """Cópia fiel do código anterior à refatoração (fila_service.py, versão original)."""
    chave = (0 if not parada else -1, nivel, prazo or datetime.max, criado_em)
    if criado_em.tzinfo is not None:
        criado_em = criado_em.astimezone(timezone.utc).replace(tzinfo=None)
    if prazo is not None and prazo.tzinfo is not None:
        prazo = prazo.astimezone(timezone.utc).replace(tzinfo=None)
    return (chave[0], chave[1], prazo or datetime.max, criado_em)


def _casos(n=500):
    rng = random.Random(7)
    base = datetime(2026, 3, 1, 8, 0, 0)
    fuso = timezone(timedelta(hours=-3))
    for _ in range(n):
        prazo = rng.choice([None, base + timedelta(days=rng.randint(1, 90)),
                            (base + timedelta(days=rng.randint(1, 90))).replace(tzinfo=fuso)])
        criado = base + timedelta(minutes=rng.randint(0, 5000))
        if rng.random() < 0.3:
            criado = criado.replace(tzinfo=timezone.utc)
        yield rng.random() < 0.3, rng.randint(1, 5), prazo, criado


def test_chave_identica_a_original_em_500_casos():
    for caso in _casos():
        assert chave_prioridade(*caso) == chave_original(*caso)


async def test_ordem_da_fila_identica_a_ordenacao_pela_chave_original():
    casos = list(_casos(120))
    fila = FilaRequisicoes()
    for i, c in enumerate(casos):
        await fila.adicionar(f"r{i}", *c)
    saida = [await asyncio.wait_for(fila.proximo(), 2) for _ in casos]
    # a fila desempata por ordem de chegada; sorted() é estável, então reproduz exatamente isso
    esperado = [f"r{i}" for i, c in sorted(enumerate(casos), key=lambda t: chave_original(*t[1]))]
    assert saida == esperado
