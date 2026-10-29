"""
Lê resultados.csv e gera: resumo.md (tabelas) e os gráficos PNG (matplotlib, backend não interativo).

    python -m benchmarks.relatorio [diretório]     # padrão: benchmarks/resultados
"""
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PADRAO = Path(__file__).resolve().parent / "resultados"
NOME = {"alta": "Alta contenção (1 material)", "baixa": "Baixa contenção (vários materiais)"}
COR = {"alta": "#c0392b", "baixa": "#1f5fbf"}


def _num(v):
    if v in ("True", "False"):
        return v == "True"
    try:
        return float(v)
    except ValueError:
        return v


def ler(csv_path: Path) -> list[dict]:
    with open(csv_path, encoding="utf-8") as f:
        return [{k: _num(v) for k, v in linha.items()} for linha in csv.DictReader(f)]


def desvio(xs):
    return st.stdev(xs) if len(xs) > 1 else 0.0


def agregar(linhas):
    """(cenario, modo, n_workers) -> estatísticas sobre as execuções medidas."""
    grupos = defaultdict(list)
    for l in linhas:
        grupos[(l["cenario"], l["modo"], int(l["n_workers"]))].append(l)
    out = {}
    for chave, ls in grupos.items():
        t = [l["tempo_s"] for l in ls]
        out[chave] = {
            "n_exec": len(ls), "t_med": st.median(t), "t_dp": desvio(t),
            "thr_med": st.median(l["throughput_rps"] for l in ls), "thr_dp": desvio([l["throughput_rps"] for l in ls]),
            "p50": st.median(l["lat_p50_ms"] for l in ls), "p95": st.median(l["lat_p95_ms"] for l in ls),
            "p99": st.median(l["lat_p99_ms"] for l in ls), "p95_dp": desvio([l["lat_p95_ms"] for l in ls]),
            "cpu": st.median(l["cpu_cliente_pct"] for l in ls), "proc": int(ls[0]["processos"]),
            "inv_ok": all(l["invariantes_ok"] for l in ls),
            "vendido_med": st.mean(l["vendido_a_mais"] for l in ls), "vendido_dp": desvio([l["vendido_a_mais"] for l in ls]),
            "vendido_max": max(l["vendido_a_mais"] for l in ls),
            "diverg_med": st.mean(l["divergencia"] for l in ls),
            "runs_com_vendido": sum(1 for l in ls if l["vendido_a_mais"] > 0),
            "inv_falhas": sum(1 for l in ls if not l["invariantes_ok"]),
            "excede_pool": any(l["excede_pool"] for l in ls),
        }
    return out


def _speedups(ag, cenario):
    base = ag.get((cenario, "baseline", 1))
    um = ag.get((cenario, "workers", 1))
    linhas = []
    for (c, modo, n), v in sorted(ag.items(), key=lambda kv: kv[0][2]):
        if c != cenario or modo != "workers":
            continue
        sp_base = base["t_med"] / v["t_med"] if base else None
        sp_um = um["t_med"] / v["t_med"] if um else None
        linhas.append((n, v, sp_base, sp_um))
    return base, linhas


def tabela_cenario(ag, cenario):
    base, linhas = _speedups(ag, cenario)
    t = ["| Execução | Workers | T mediano (s) | Desvio (s) | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) "
         "| Speedup vs sequencial | Eficiência | Speedup vs 1 worker | CPU do cliente | Invariantes |",
         "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|:-:|"]

    def linha(nome, n, v, sb, su):
        ef = f"{sb / n:.2f}" if sb else "—"
        su = f"{su:.2f}" if su else "—"
        return (f"| {nome} | {n} | {v['t_med']:.2f} | {v['t_dp']:.2f} | {v['thr_med']:.1f} | {v['p50']:.0f} | {v['p95']:.0f} "
                f"| {v['p99']:.0f} | {sb:.2f} | {ef} | {su} | {v['cpu']:.0f}% | {'OK' if v['inv_ok'] else 'FALHOU'} |")

    um = ag.get((cenario, "workers", 1))
    if base:
        t.append(f"| Sequencial (sem fila/workers) | 1 | {base['t_med']:.2f} | {base['t_dp']:.2f} | {base['thr_med']:.1f} "
                 f"| {base['p50']:.0f} | {base['p95']:.0f} | {base['p99']:.0f} | 1.00 | — | — | {base['cpu']:.0f}% "
                 f"| {'OK' if base['inv_ok'] else 'FALHOU'} |")
    for n, v, sb, su in linhas:
        nome = f"1 processo, {n} worker(s)" + (" ⚠️ > pool" if v["excede_pool"] else "")
        t.append(linha(nome, n, v, sb, su))
    for (c, modo, n), v in sorted(ag.items(), key=lambda kv: kv[0][2]):
        if c == cenario and modo == "multiproc" and base:
            sb = base["t_med"] / v["t_med"]
            su = um["t_med"] / v["t_med"] if um else None
            t.append(linha(f"{v['proc']} processos × 4 workers", n, v, sb, su))
    return "\n".join(t)


def tabela_controle(ag):  # noqa
    ns = sorted({n for (c, m, n) in ag if m in ("com_lock", "sem_lock")})
    t = ["| Workers | Modo | Execuções com venda a mais | Unidades vendidas a mais (média ± dp) | Máximo | Divergência contábil média (un.) | Invariantes |",
         "|--:|---|--:|--:|--:|--:|:-:|"]
    for n in ns:
        for modo, rot in (("com_lock", "COM lock (FOR UPDATE)"), ("sem_lock", "SEM lock")):
            v = ag.get(("alta", modo, n))
            if v:
                t.append(f"| {n} | {rot} | {v['runs_com_vendido']}/{v['n_exec']} | {v['vendido_med']:.1f} ± {v['vendido_dp']:.1f} "
                         f"| {v['vendido_max']:.0f} | {v['diverg_med']:.1f} | {'OK' if v['inv_ok'] else f'FALHOU em {v['inv_falhas']}/{v['n_exec']}'} |")
    return "\n".join(t)


def tabela_versoes(ag):
    dec = any(k[1] == "v_atual_sem_idx" for k in ag)
    t = ["| Cenário | Workers | Anterior (req/s) | Atual (req/s) | Variação | Tempo anterior (s) | Tempo atual (s) | CPU anterior | CPU atual |"
         + (" Atual sem índices novos (req/s) | Variação só do INSERT |" if dec else ""),
         "|---|--:|--:|--:|--:|--:|--:|--:|--:|" + ("--:|--:|" if dec else "")]
    chaves = sorted({(c, n) for (c, m, n) in ag if m == "v_atual"})
    for c, n in chaves:
        a, b = ag.get((c, "v_anterior", n)), ag.get((c, "v_atual", n))
        if a and b:
            var = (b["thr_med"] / a["thr_med"] - 1) * 100
            t.append(f"| {NOME[c].split(' (')[0]} | {n} | {a['thr_med']:.0f} ± {a['thr_dp']:.0f} | {b['thr_med']:.0f} ± {b['thr_dp']:.0f} "
                     f"| {var:+.1f}% | {a['t_med']:.2f} | {b['t_med']:.2f} | {a['cpu']:.0f}% | {b['cpu']:.0f}% |"
                     + (lambda x: f" {x['thr_med']:.0f} ± {x['thr_dp']:.0f} | {(x['thr_med'] / a['thr_med'] - 1) * 100:+.1f}% |"
                        if x else " — | — |")(ag.get((c, "v_atual_sem_idx", n)) if dec else None))
    return "\n".join(t)


def graficos(ag, saida: Path):
    cenarios = [c for c in ("alta", "baixa") if any(k[0] == c and k[1] == "workers" for k in ag)]

    def pontos(c, campo):
        xs = sorted(n for (cc, m, n) in ag if cc == c and m == "workers")
        return xs, [ag[(c, "workers", n)][campo] for n in xs]

    if cenarios:   # gráficos que dependem da execução com N workers
        # 1) throughput x workers
        fig, ax = plt.subplots(figsize=(7, 4.2))
        for c in cenarios:
            xs, ys = pontos(c, "thr_med")
            _, er = pontos(c, "thr_dp")
            ax.errorbar(xs, ys, yerr=er, marker="o", capsize=3, color=COR[c], label=NOME[c])
            mp = sorted(n for (cc, m, n) in ag if cc == c and m == "multiproc")
            if mp:
                ax.plot(mp, [ag[(c, "multiproc", n)]["thr_med"] for n in mp], marker="s", ls="--", color=COR[c],
                        label=f"Multiprocesso (4 workers/processo) — {NOME[c].split(' (')[0]}")
            if (c, "baseline", 1) in ag:
                ax.axhline(ag[(c, "baseline", 1)]["thr_med"], color=COR[c], ls=":", lw=1, label=f"Sequencial — {NOME[c].split(' (')[0]}")
        ax.set(xlabel="Número de workers", ylabel="Throughput (requisições/s)", title="Throughput x workers", xscale="log", xticks=xs)
        ax.set_xticklabels(xs); ax.grid(alpha=.3); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(saida / "grafico_throughput.png", dpi=140); plt.close(fig)

        # 2) speedup x workers (vs sequencial) com a linha ideal
        fig, ax = plt.subplots(figsize=(7, 4.2))
        for c in cenarios:
            base, linhas = _speedups(ag, c)
            if base:
                ax.plot([n for n, *_ in linhas], [sb for _, _, sb, _ in linhas], marker="o", color=COR[c], label=NOME[c])
                mp = sorted(n for (cc, m, n) in ag if cc == c and m == "multiproc")
                if mp:
                    ax.plot(mp, [base["t_med"] / ag[(c, "multiproc", n)]["t_med"] for n in mp], marker="s", ls="--",
                            color=COR[c], label=f"Multiprocesso — {NOME[c].split(' (')[0]}")
        xs_all = sorted({n for (_, m, n) in ag if m in ("workers", "multiproc")})
        ax.plot(xs_all, xs_all, "k--", lw=1, label="Speedup ideal (linear)")
        ax.set(xlabel="Número de workers", ylabel="Speedup vs execução sequencial", title="Speedup x workers",
               xscale="log", yscale="log", xticks=xs_all, yticks=[0.5, 1, 2, 4, 8, 16])
        ax.set_xticklabels(xs_all); ax.set_yticklabels(["0,5", "1", "2", "4", "8", "16"]); ax.grid(alpha=.3, which="both"); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(saida / "grafico_speedup.png", dpi=140); plt.close(fig)

        # 3) latência p95 x workers
        fig, ax = plt.subplots(figsize=(7, 4.2))
        for c in cenarios:
            xs, ys = pontos(c, "p95")
            ax.plot(xs, [y / 1000 for y in ys], marker="o", color=COR[c], label=NOME[c])
        ax.set(xlabel="Número de workers", ylabel="Latência p95 (s) — com todo o backlog chegando em t=0",
               title="Latência p95 x workers", xscale="log", xticks=xs); ax.set_xticklabels(xs); ax.grid(alpha=.3); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(saida / "grafico_latencia_p95.png", dpi=140); plt.close(fig)

    # 4) controle negativo: vendido a mais + divergência contábil
    ns = sorted({n for (c, m, n) in ag if m in ("com_lock", "sem_lock")})
    if ns:
        fig, eixos = plt.subplots(1, 2, figsize=(11, 4.2))
        paineis = (("vendido_med", "vendido_dp", "Unidades vendidas a mais (média)", "Material entregue acima do estoque"),
                   ("diverg_med", None, "Divergência contábil (unidades)", "Entregue − baixa registrada no estoque"))
        larg, xs = 0.38, range(len(ns))
        for ax, (campo, dp, rotulo, titulo) in zip(eixos, paineis):
            for i, (modo, cor, rot) in enumerate((("com_lock", "#1b8a4b", "COM lock (FOR UPDATE)"), ("sem_lock", "#c0392b", "SEM lock"))):
                vals = [ag.get(("alta", modo, n), {}).get(campo, 0) for n in ns]
                erros = [ag.get(("alta", modo, n), {}).get(dp, 0) for n in ns] if dp else None
                barras = ax.bar([x + (i - .5) * larg for x in xs], vals, larg, yerr=erros, capsize=3, color=cor, label=rot)
                ax.bar_label(barras, fmt="%.0f", fontsize=8, padding=2)
            ax.set(xlabel="Número de workers", ylabel=rotulo, title=titulo, xticks=list(xs))
            ax.set_xticklabels(ns); ax.grid(alpha=.3, axis="y"); ax.legend(fontsize=8)
        fig.suptitle("Controle negativo: condição de corrida sem lock (alta contenção)")
        fig.tight_layout(); fig.savefig(saida / "grafico_controle_lock.png", dpi=140); plt.close(fig)

    if cenarios:
        # 5) eficiência
        fig, ax = plt.subplots(figsize=(7, 4.2))
        for c in cenarios:
            base, linhas = _speedups(ag, c)
            if base:
                ax.plot([n for n, *_ in linhas], [sb / n for n, _, sb, _ in linhas], marker="o", color=COR[c], label=NOME[c])
        ax.axhline(1, color="k", ls="--", lw=1, label="Eficiência ideal")
        xs_all = sorted({n for (_, m, n) in ag if m == "workers"})
        ax.set(xlabel="Número de workers", ylabel="Eficiência = speedup / N", title="Eficiência paralela", xscale="log", xticks=xs_all)
        ax.set_xticklabels(xs_all); ax.grid(alpha=.3); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(saida / "grafico_eficiencia.png", dpi=140); plt.close(fig)

    # 6) comparação de versões (A/B): throughput anterior x atual
    pares = sorted({(c, n) for (c, m, n) in ag if m == "v_atual"})
    if pares:
        fig, ax = plt.subplots(figsize=(8, 4.2))
        larg, xs_v = 0.38, range(len(pares))
        for i, (modo, cor_, rot) in enumerate((("v_anterior", "#7f8c8d", "Anterior (sem livro-razão, sem índices novos)"),
                                               ("v_atual", "#1f5fbf", "Atual (livro-razão + índices)"))):
            vals = [ag.get((c, modo, n), {}).get("thr_med", 0) for c, n in pares]
            dps = [ag.get((c, modo, n), {}).get("thr_dp", 0) for c, n in pares]
            b = ax.bar([x + (i - .5) * larg for x in xs_v], vals, larg, yerr=dps, capsize=3, color=cor_, label=rot)
            ax.bar_label(b, fmt="%.0f", fontsize=8, padding=2)
        ax.set(ylabel="Throughput (req/s)", title="Antes x depois do livro-razão (execuções intercaladas)", xticks=list(xs_v))
        ax.set_xticklabels([f"{NOME[c].split(' (')[0]}\n{n} worker(s)" for c, n in pares], fontsize=8)
        ax.grid(alpha=.3, axis="y"); ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(saida / "grafico_versoes.png", dpi=140); plt.close(fig)


def gerar(saida: Path | str = PADRAO):
    saida = Path(saida)
    ag = agregar(ler(saida / "resultados.csv"))
    amb = {}
    if (saida / "ambiente.json").exists():
        amb = json.loads((saida / "ambiente.json").read_text(encoding="utf-8"))
    partes = ["# Resultados do benchmark (gerado automaticamente)\n"]
    if amb:
        p = amb.get("parametros", {})
        partes += ["## Ambiente\n"] + [f"- **{k}**: {v}" for k, v in amb.items() if k != "parametros"]
        partes += [f"- **parâmetros**: {p.get('requisicoes')} requisições, {p.get('materiais')} materiais (baixa contenção), "
                   f"{p.get('execucoes')} execuções medidas + 1 de aquecimento, semente {p.get('semente')}\n"]
    for c in ("alta", "baixa"):
        if any(k[0] == c and k[1] == "workers" for k in ag):
            partes += [f"## {NOME[c]}\n", tabela_cenario(ag, c), ""]
    if any(k[1] in ("com_lock", "sem_lock") for k in ag):
        partes += ["## Controle negativo: com lock x sem lock (alta contenção)\n", tabela_controle(ag), ""]
    if any(k[1] == "v_atual" for k in ag):
        partes += ["## Comparação de versões (A/B intercalado): antes x depois do livro-razão\n", tabela_versoes(ag), ""]
    graficos(ag, saida)
    partes += ["## Gráficos\n"] + [f"![{n}]({n}.png)" for n in (
        "grafico_throughput", "grafico_speedup", "grafico_latencia_p95", "grafico_controle_lock", "grafico_eficiencia",
        "grafico_versoes") if (saida / f"{n}.png").exists()]
    (saida / "resumo.md").write_text("\n".join(partes) + "\n", encoding="utf-8")
    print(f"Relatório: {saida / 'resumo.md'} (+ gráficos PNG)")


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else PADRAO)
