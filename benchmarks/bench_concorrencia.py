"""
Benchmark de concorrência do motor de alocação de estoque.

As configurações são executadas INTERCALADAS (uma rodada = todas as configurações, em ordem aleatória),
para que a deriva de desempenho do ambiente não se confunda com o efeito do nº de workers.

    python -m benchmarks.bench_concorrencia --rapido      # versão curta (~1 min)
    python -m benchmarks.bench_concorrencia               # completa

Roda em banco próprio (BENCH_DATABASE_URL, padrão .../estoque_bench), com a MESMA configuração de
engine/pool da aplicação, e NÃO usa a fila global: cria a sua FilaRequisicoes e inicia N `worker_loop`
de produção. Os dados são sempre os mesmos (semente fixa) para todas as configurações.
"""
# --- o banco precisa ser definido ANTES de importar app.* (o engine nasce no import) ---
import os

from devtools.dbutil import garantir_banco, limpar_dados, recriar_schema, url_bench, validar_url_descartavel

BENCH_URL = validar_url_descartavel(url_bench())
os.environ["DATABASE_URL"] = BENCH_URL

import argparse  # noqa: E402
import asyncio  # noqa: E402
import concurrent.futures  # noqa: E402
import multiprocessing  # noqa: E402
import csv  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import platform  # noqa: E402
import random  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.models import Material, Obra, Requisicao, StatusRequisicao  # noqa: E402
from app.services.estoque_service import processar_requisicao  # noqa: E402
from app.services.fila_service import FilaRequisicoes, worker_loop  # noqa: E402
from benchmarks.sem_lock import processar_requisicao_sem_lock  # noqa: E402
from benchmarks.versao_anterior import INDICES_NOVOS, processar_requisicao_v1  # noqa: E402

RESULTADOS = Path(__file__).resolve().parent / "resultados"
FRACAO_ESTOQUE = 0.6          # estoque = 60% da demanda de cada material: há déficit (parciais/negadas)
N_OBRAS = 10
POOL_TOTAL = 30               # pool_size=20 + max_overflow=10 em app/core/database.py

CAMPOS = [
    "cenario", "modo", "n_workers", "processos", "execucao", "requisicoes", "materiais", "tempo_s", "throughput_rps",
    "lat_p50_ms", "lat_p95_ms", "lat_p99_ms", "lat_max_ms", "invariantes_ok", "estoque_final_min",
    "estoque_inicial_total", "atendido_total", "vendido_a_mais", "divergencia",
    "aprovada", "parcial", "negada", "erro", "abertas", "excede_pool", "cpu_cliente_pct",
]
WORKERS_POR_PROCESSO = 4      # modo multiprocesso: cada processo roda 4 workers

logging.getLogger("fila_service").setLevel(logging.CRITICAL)   # sem ruído de log por requisição


async def ajustar_indices(ativos: bool):
    """Liga/desliga os índices que a mudança do livro-razão acrescentou (a tabela está pequena: é instantâneo)."""
    async with engine.begin() as conn:
        for nome, alvo in INDICES_NOVOS.items():
            await conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS {nome} ON {alvo}" if ativos else f"DROP INDEX IF EXISTS {nome}")


def agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)     # datetimes "naive" em UTC, como o projeto


# ---------------------------------------------------------------- carga de trabalho

@dataclass
class Carga:
    reqs: list            # (id, parada, nivel, prazo, criado_em) — o que a fila precisa
    estoque_inicial: dict  # material_id -> quantidade


async def preparar(n_req: int, n_mat: int, semente: int) -> Carga:
    """Zera o banco e cria obras, materiais e requisições (determinístico). Fora da medição."""
    await limpar_dados(BENCH_URL, Base.metadata)
    rng = random.Random(semente)
    base = agora()
    async with AsyncSessionLocal() as db:
        obras = [Obra(nome=f"Obra {i}", cidade="Recife", nivel_prioridade=rng.randint(1, 5),
                      parada_por_falta_material=(i < 2),
                      prazo_entrega=None if i % 3 == 0 else base + timedelta(days=rng.randint(5, 60)))
                 for i in range(N_OBRAS)]
        materiais = [Material(nome=f"Material {i:03d}", unidade="un", quantidade_estoque=0, estoque_critico=0)
                     for i in range(n_mat)]
        db.add_all(obras + materiais)
        await db.flush()

        demanda = {m.id: 0.0 for m in materiais}
        reqs = []
        for i in range(n_req):
            obra, mat, qtd = rng.choice(obras), rng.choice(materiais), float(rng.randint(1, 5))
            demanda[mat.id] += qtd
            r = Requisicao(id=str(uuid.uuid4()), obra_id=obra.id, material_id=mat.id,
                           quantidade_solicitada=qtd, criado_em=base + timedelta(milliseconds=i))
            reqs.append((r, obra))
        for m in materiais:
            m.quantidade_estoque = float(max(1, round(demanda[m.id] * FRACAO_ESTOQUE)))
        db.add_all(r for r, _ in reqs)
        await db.commit()
        estoque = {m.id: m.quantidade_estoque for m in materiais}
        itens = [(r.id, o.parada_por_falta_material, o.nivel_prioridade, o.prazo_entrega, r.criado_em)
                 for r, o in reqs]
    return Carga(itens, estoque)


async def montar_fila(carga: Carga) -> FilaRequisicoes:
    fila = FilaRequisicoes()
    for item in carga.reqs:
        await fila.adicionar(*item)
    return fila


# ---------------------------------------------------------------- execução

async def esperar_conclusao(fila: FilaRequisicoes, monitor):
    """Espera esvaziar a fila e depois todas as requisições saírem de PENDENTE/EM_PROCESSAMENTO."""
    while fila.tamanho() > 0:
        await asyncio.sleep(0.005)
    while True:
        async with monitor() as db:
            abertas = (await db.execute(text(
                "SELECT count(*) FROM requisicoes WHERE status IN ('PENDENTE','EM_PROCESSAMENTO')"))).scalar_one()
        if abertas == 0:
            return
        await asyncio.sleep(0.005)


async def loop_generico(worker_id: str, fila: FilaRequisicoes, processor):
    """Igual ao worker_loop de produção, mas com o processador injetável (usado no controle negativo)."""
    while True:
        rid = await fila.proximo()
        if rid is None:
            continue
        try:
            async with AsyncSessionLocal() as db:
                await processor(db, rid, worker_id)
        except Exception:
            logging.getLogger("bench").exception("erro processando %s", rid)


async def rodar_workers(carga: Carga, n_workers: int, monitor, processor=None):
    """Retorna (tempo_total_s, T0, extras). Sem `processor`, usa o worker_loop REAL de produção."""
    fila = await montar_fila(carga)           # backlog completo formado antes de os workers existirem
    t0 = agora()
    cpu0 = time.process_time()
    ini = time.perf_counter()
    if processor is None:
        tarefas = [asyncio.create_task(worker_loop(f"worker-{i + 1}", fila)) for i in range(n_workers)]
    else:
        tarefas = [asyncio.create_task(loop_generico(f"worker-{i + 1}", fila, processor)) for i in range(n_workers)]
    try:
        await esperar_conclusao(fila, monitor)
        tempo = time.perf_counter() - ini
        cpu = time.process_time() - cpu0
    finally:
        for t in tarefas:
            t.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
    return tempo, t0, {"cpu_cliente_pct": round(cpu / tempo * 100, 1), "processos": 1}


async def rodar_sequencial(carga: Carga):
    """Baseline: mesmas requisições, na ordem de prioridade, uma a uma, num único laço (sem fila/workers)."""
    fila = await montar_fila(carga)
    ordem = [await fila.proximo() for _ in carga.reqs]
    t0 = agora()
    cpu0 = time.process_time()
    ini = time.perf_counter()
    for rid in ordem:
        async with AsyncSessionLocal() as db:
            await processar_requisicao(db, rid, "sequencial")
    tempo = time.perf_counter() - ini
    return tempo, t0, {"cpu_cliente_pct": round((time.process_time() - cpu0) / tempo * 100, 1), "processos": 1}


# ---- multiprocesso: P processos, cada um com sua fila + K workers (como `uvicorn --workers P`) ----

def _dormir(segundos: float):
    time.sleep(segundos)       # só para forçar o ProcessPoolExecutor a criar todos os processos


def _processo_filho(itens: list, n_workers: int, inicio_epoch: float):
    return asyncio.run(_filho_async(itens, n_workers, inicio_epoch))


async def _filho_async(itens, n_workers, inicio_epoch):
    """Roda no processo filho: aquece conexões, espera a largada comum e processa o seu pedaço."""
    fila = FilaRequisicoes()
    for item in itens:
        await fila.adicionar(*item)
    ids = [i[0] for i in itens]
    mon = create_async_engine(BENCH_URL, pool_size=1, max_overflow=0)
    try:
        async def aquecer():                      # abre as conexões antes da largada (fora da medição)
            async with AsyncSessionLocal() as db:
                await db.execute(text("select 1"))
                await asyncio.sleep(0.2)
        await asyncio.gather(*[aquecer() for _ in range(n_workers)])
        await asyncio.sleep(max(0.0, inicio_epoch - time.time()))
        cpu0 = time.process_time()
        tarefas = [asyncio.create_task(worker_loop(f"worker-{i + 1}", fila)) for i in range(n_workers)]
        while fila.tamanho() > 0:
            await asyncio.sleep(0.005)
        while True:
            async with mon.connect() as conn:
                abertas = (await conn.execute(text(
                    "SELECT count(*) FROM requisicoes WHERE id = ANY(:ids) AND status IN ('PENDENTE','EM_PROCESSAMENTO')"),
                    {"ids": ids})).scalar_one()
            if abertas == 0:
                break
            await asyncio.sleep(0.005)
        fim, cpu = time.time(), time.process_time() - cpu0
        for t in tarefas:
            t.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        return fim, cpu
    finally:
        await mon.dispose()
        await engine.dispose()                     # o loop deste filho acaba: nada de conexão presa a ele


async def rodar_multiprocesso(carga: Carga, n_proc: int, pool: concurrent.futures.ProcessPoolExecutor):
    """Divide as requisições (em ordem de prioridade) entre `n_proc` processos e mede até o último terminar."""
    fila = await montar_fila(carga)
    por_id = {i[0]: i for i in carga.reqs}
    ordem = [por_id[await fila.proximo()] for _ in carga.reqs]
    partes = [ordem[i::n_proc] for i in range(n_proc)]
    inicio = time.time() + 3.0                      # largada comum, com folga para todos estarem prontos
    loop = asyncio.get_running_loop()
    futuros = [loop.run_in_executor(pool, _processo_filho, partes[i], WORKERS_POR_PROCESSO, inicio)
               for i in range(n_proc)]
    resultados = await asyncio.gather(*futuros)
    tempo = max(f for f, _ in resultados) - inicio
    cpu = sum(c for _, c in resultados)
    t0 = datetime.fromtimestamp(inicio, timezone.utc).replace(tzinfo=None)
    return tempo, t0, {"cpu_cliente_pct": round(cpu / tempo * 100, 1), "processos": n_proc}


async def coletar(carga: Carga, t0: datetime, tempo: float, extras: dict, **rotulos) -> dict:
    async with AsyncSessionLocal() as db:
        reqs = (await db.execute(select(
            Requisicao.status, Requisicao.quantidade_atendida, Requisicao.processado_em))).all()
        estoques = {i: q for i, q in (await db.execute(select(Material.id, Material.quantidade_estoque))).all()}
        atendido_mat = dict((await db.execute(text(
            "SELECT material_id, coalesce(sum(quantidade_atendida),0) FROM requisicoes GROUP BY material_id"))).all())

    cont = {s: sum(1 for r in reqs if r.status == s) for s in StatusRequisicao}
    lats = np.array([(r.processado_em - t0).total_seconds() * 1000 for r in reqs if r.processado_em], dtype=float)
    inicial_total = sum(carga.estoque_inicial.values())
    atendido_total = float(sum(atendido_mat.values()))
    # conservação por material: inicial == final + atendido
    viola_conservacao = [m for m, ini in carga.estoque_inicial.items()
                         if abs(ini - (estoques[m] + float(atendido_mat.get(m, 0)))) > 1e-6]
    final_min = min(estoques.values())
    # "vendido a mais": por material, quanto saiu além do que existia (soma só dos excessos)
    vendido_a_mais = sum(max(0.0, float(atendido_mat.get(m, 0)) - ini) for m, ini in carga.estoque_inicial.items())
    divergencia = sum(float(atendido_mat.get(m, 0)) - (ini - estoques[m]) for m, ini in carga.estoque_inicial.items())
    abertas = cont[StatusRequisicao.PENDENTE] + cont[StatusRequisicao.EM_PROCESSAMENTO]
    ok = final_min >= 0 and not viola_conservacao and abertas == 0 and cont[StatusRequisicao.ERRO] == 0
    n = len(reqs)
    return {
        **rotulos, **extras, "requisicoes": n, "materiais": len(estoques),
        "tempo_s": round(tempo, 4), "throughput_rps": round(n / tempo, 2),
        "lat_p50_ms": round(float(np.percentile(lats, 50)), 1), "lat_p95_ms": round(float(np.percentile(lats, 95)), 1),
        "lat_p99_ms": round(float(np.percentile(lats, 99)), 1), "lat_max_ms": round(float(lats.max()), 1),
        "invariantes_ok": ok, "estoque_final_min": final_min,
        "estoque_inicial_total": inicial_total, "atendido_total": atendido_total,
        "vendido_a_mais": vendido_a_mais, "divergencia": divergencia,
        "aprovada": cont[StatusRequisicao.APROVADA], "parcial": cont[StatusRequisicao.PARCIAL],
        "negada": cont[StatusRequisicao.NEGADA], "erro": cont[StatusRequisicao.ERRO], "abertas": abertas,
        "excede_pool": rotulos.get("n_workers", 0) > POOL_TOTAL,
    }


# ---------------------------------------------------------------- orquestração

async def ambiente() -> dict:
    async with engine.connect() as conn:
        pg = (await conn.execute(text("select version()"))).scalar_one()
    try:
        ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Ports}}"],
                            capture_output=True, text=True, timeout=10).stdout.splitlines()
        cont = [l for l in ps if "postgres" in l.lower()]
        docker = f"sim ({'; '.join(cont)})" if cont else "não detectado"
    except Exception:
        docker = "desconhecido (docker indisponível)"
    return {
        "data": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version.split()[0], "so": platform.platform(), "processador": platform.processor(),
        "cpus_logicas": os.cpu_count(), "postgresql": pg.split(",")[0], "postgres_em_docker": docker,
        "pool_size": engine.pool.size(), "max_overflow": engine.pool._max_overflow,
        "banco": BENCH_URL.rsplit("/", 1)[-1],
    }


def gravar(writer, arquivo, linha):
    writer.writerow({c: linha.get(c, "") for c in CAMPOS})
    arquivo.flush()


def mostrar(linha, aquecimento=False):
    tag = "aquecimento" if aquecimento else f"exec {linha['execucao']}"
    print(f"  [{linha['cenario']:5}] {linha['modo']:9} N={linha['n_workers']:<2} {tag:12} "
          f"T={linha['tempo_s']:7.2f}s  {linha['throughput_rps']:8.1f} req/s  p95={linha['lat_p95_ms']:9.1f}ms  "
          f"cpu={linha['cpu_cliente_pct']:5.0f}%  vendido_a_mais={linha['vendido_a_mais']:<6g} inv={'OK' if linha['invariantes_ok'] else 'FALHOU'}", flush=True)


async def principal(a):
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    await garantir_banco(BENCH_URL)
    await recriar_schema(BENCH_URL, Base.metadata)
    amb = await ambiente()
    amb["parametros"] = {k: v for k, v in vars(a).items()}
    (saida / "ambiente.json").write_text(json.dumps(amb, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Ambiente:", json.dumps({k: v for k, v in amb.items() if k != "parametros"}, ensure_ascii=False))

    # conexão única e persistente só para o monitor de conclusão (não disputa o pool dos workers)
    mon_engine = create_async_engine(BENCH_URL, pool_size=1, max_overflow=0)
    from sqlalchemy.ext.asyncio import async_sessionmaker
    monitor = async_sessionmaker(bind=mon_engine, expire_on_commit=False)

    # Monta TODAS as configurações e as executa INTERCALADAS: em cada rodada, todas as configurações
    # rodam uma vez, em ordem aleatória (semente fixa). O desempenho desta máquina (Windows + Docker)
    # muda de fase ao longo de minutos; executar uma configuração inteira em bloco misturaria essa
    # deriva com o efeito de variar workers. Intercalando, todas enfrentam as mesmas condições.
    configs = []   # (cenario, modo, n_workers, n_materiais, fabrica_do_executor, indices_novos_ligados)
    for cenario in a.cenarios:
        n_mat = 1 if cenario == "alta" else a.materiais
        if not a.sem_baseline:
            configs.append((cenario, "baseline", 1, n_mat, lambda: (lambda c: rodar_sequencial(c)), True))
        for n in a.workers:
            configs.append((cenario, "workers", n, n_mat, lambda n=n: (lambda c: rodar_workers(c, n, monitor)), True))
        for p in a.processos:
            if p >= 2:
                configs.append((cenario, "multiproc", p * WORKERS_POR_PROCESSO, n_mat,
                                lambda p=p: (lambda c: rodar_multiprocesso(c, p, pools[p])), True))
        if a.comparar_versoes:   # A/B: função congelada sem os índices novos x produção atual com os índices
            for n in a.workers_comparacao:
                configs.append((cenario, "v_anterior", n, n_mat, lambda n=n: (
                    lambda c: rodar_workers(c, n, monitor, processar_requisicao_v1)), False))
                configs.append((cenario, "v_atual", n, n_mat, lambda n=n: (
                    lambda c: rodar_workers(c, n, monitor, processar_requisicao)), True))
                if a.decompor_indices:   # código atual SEM os índices novos: separa o custo do INSERT do custo dos índices
                    configs.append((cenario, "v_atual_sem_idx", n, n_mat, lambda n=n: (
                        lambda c: rodar_workers(c, n, monitor, processar_requisicao)), False))
    if not a.sem_controle:
        for n in a.workers_controle:
            configs.append(("alta", "com_lock", n, 1, lambda n=n: (
                lambda c: rodar_workers(c, n, monitor, processar_requisicao)), True))
            configs.append(("alta", "sem_lock", n, 1, lambda n=n: (
                lambda c: rodar_workers(c, n, monitor, processar_requisicao_sem_lock)), True))

    # um pool de processos persistente por nº de processos (criado antes de medir, fora do tempo)
    pools = {}
    for p in sorted({p for p in a.processos if p >= 2}):
        pools[p] = concurrent.futures.ProcessPoolExecutor(
            max_workers=p, mp_context=multiprocessing.get_context("spawn"))
        await asyncio.gather(*[asyncio.get_running_loop().run_in_executor(pools[p], _dormir, 1.0) for _ in range(p)])

    sorteio = random.Random(a.semente)
    print(f"\n{len(configs)} configurações x ({a.execucoes} execuções + 1 de aquecimento), "
          f"{a.requisicoes} requisições cada, intercaladas em ordem aleatória por rodada.")
    with open(saida / "resultados.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        inicio = time.perf_counter()
        for rep in range(0, a.execucoes + 1):          # rodada 0 = aquecimento (descartada)
            print(f"\n--- rodada {rep}" + (" (aquecimento, descartada)" if rep == 0 else f" de {a.execucoes}") + " ---", flush=True)
            ordem = configs[:]
            sorteio.shuffle(ordem)
            for cenario, modo, n, n_mat, fabrica, indices in ordem:
                carga = await preparar(a.requisicoes, n_mat, a.semente)
                await ajustar_indices(indices)
                tempo, t0, extras = await fabrica()(carga)
                linha = await coletar(carga, t0, tempo, extras, cenario=cenario, modo=modo, n_workers=n, execucao=rep)
                mostrar(linha, aquecimento=(rep == 0))
                if rep > 0:
                    gravar(w, f, linha)
        print(f"\nConcluído em {(time.perf_counter() - inicio) / 60:.1f} min. CSV: {saida / 'resultados.csv'}")
    for pool in pools.values():
        pool.shutdown()

    await mon_engine.dispose()
    await engine.dispose()
    from benchmarks.relatorio import gerar
    gerar(saida)


def ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def main():
    p = argparse.ArgumentParser(description="Benchmark de concorrência do estoque")
    p.add_argument("--requisicoes", type=int, default=2000, help="requisições por execução (padrão 2000)")
    p.add_argument("--materiais", type=int, default=50, help="materiais no cenário de baixa contenção (padrão 50)")
    p.add_argument("--workers", type=ints, default=[1, 2, 4, 8, 16], help="ex.: 1,2,4,8,16,32")
    p.add_argument("--workers-controle", type=ints, default=[1, 4, 16], help="workers do controle negativo")
    p.add_argument("--processos", type=ints, default=[2, 4], help=f"modo multiprocesso: nº de processos com {WORKERS_POR_PROCESSO} workers cada (vazio = pula)")
    p.add_argument("--execucoes", type=int, default=5, help="execuções medidas por configuração (+1 de aquecimento)")
    p.add_argument("--cenarios", type=lambda s: s.split(","), default=["alta", "baixa"])
    p.add_argument("--sem-controle", action="store_true", help="pula o controle negativo (sem lock)")
    p.add_argument("--sem-baseline", action="store_true", help="pula o baseline sequencial")
    p.add_argument("--comparar-versoes", action="store_true",
                   help="A/B intercalado: processamento ANTERIOR (sem livro-razão e sem índices novos) x ATUAL")
    p.add_argument("--decompor-indices", action="store_true",
                   help="no A/B de versões, inclui o código atual SEM os índices novos (separa INSERT x índices)")
    p.add_argument("--workers-comparacao", type=ints, default=[1, 4, 16], help="workers do A/B de versões")
    p.add_argument("--semente", type=int, default=42)
    p.add_argument("--saida", default=None, help="diretório de saída (padrão benchmarks/resultados[/rapido])")
    p.add_argument("--rapido", action="store_true", help="versão curta de teste (300 req, 10 materiais, 3 execuções)")
    a = p.parse_args()
    if a.rapido:
        a.requisicoes, a.materiais, a.workers, a.workers_controle, a.execucoes = 300, 10, [1, 4, 16], [1, 16], 2
        a.processos = [2]
    if a.saida is None:
        a.saida = str(RESULTADOS / ("rapido" if a.rapido else ""))
    asyncio.run(principal(a))


if __name__ == "__main__":
    main()
