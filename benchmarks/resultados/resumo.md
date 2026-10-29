# Resultados do benchmark (gerado automaticamente)

## Ambiente

- **data**: 2026-10-28 23:05:59
- **python**: 3.13.12
- **so**: Windows-11-10.0.26200-SP0
- **processador**: AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD
- **cpus_logicas**: 16
- **postgresql**: PostgreSQL 16.15 on x86_64-pc-linux-musl
- **postgres_em_docker**: sim (estoque_postgres|postgres:16-alpine|0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp)
- **pool_size**: 20
- **max_overflow**: 10
- **banco**: estoque_bench
- **parâmetros**: 2000 requisições, 50 materiais (baixa contenção), 5 execuções medidas + 1 de aquecimento, semente 42

## Alta contenção (1 material)

| Execução | Workers | T mediano (s) | Desvio (s) | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Speedup vs sequencial | Eficiência | Speedup vs 1 worker | CPU do cliente | Invariantes |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|:-:|
| Sequencial (sem fila/workers) | 1 | 19.35 | 1.33 | 103.4 | 9944 | 18433 | 19146 | 1.00 | — | — | 42% | OK |
| 1 processo, 1 worker(s) | 1 | 17.52 | 1.03 | 114.2 | 9122 | 16718 | 17359 | 1.10 | 1.10 | 1.00 | 42% | OK |
| 1 processo, 2 worker(s) | 2 | 10.56 | 1.57 | 189.3 | 5250 | 10008 | 10448 | 1.83 | 0.92 | 1.66 | 72% | OK |
| 1 processo, 4 worker(s) | 4 | 8.63 | 0.46 | 231.8 | 4626 | 8241 | 8551 | 2.24 | 0.56 | 2.03 | 84% | OK |
| 1 processo, 8 worker(s) | 8 | 8.81 | 0.64 | 227.0 | 4808 | 8449 | 8736 | 2.20 | 0.27 | 1.99 | 84% | OK |
| 1 processo, 16 worker(s) | 16 | 9.59 | 0.66 | 208.6 | 5207 | 9168 | 9476 | 2.02 | 0.13 | 1.83 | 83% | OK |
| 2 processos × 4 workers | 8 | 10.24 | 2.14 | 195.2 | 5381 | 9667 | 10085 | 1.89 | 0.24 | 1.71 | 104% | OK |
| 4 processos × 4 workers | 16 | 8.69 | 2.61 | 230.2 | 4891 | 8287 | 8581 | 2.23 | 0.14 | 2.02 | 114% | OK |

## Baixa contenção (vários materiais)

| Execução | Workers | T mediano (s) | Desvio (s) | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Speedup vs sequencial | Eficiência | Speedup vs 1 worker | CPU do cliente | Invariantes |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|:-:|
| Sequencial (sem fila/workers) | 1 | 18.35 | 1.72 | 109.0 | 9614 | 17507 | 18169 | 1.00 | — | — | 42% | OK |
| 1 processo, 1 worker(s) | 1 | 18.67 | 1.26 | 107.1 | 10041 | 17874 | 18496 | 0.98 | 0.98 | 1.00 | 42% | OK |
| 1 processo, 2 worker(s) | 2 | 9.98 | 2.08 | 200.4 | 5145 | 9547 | 9887 | 1.84 | 0.92 | 1.87 | 73% | OK |
| 1 processo, 4 worker(s) | 4 | 7.00 | 0.43 | 285.6 | 3624 | 6640 | 6926 | 2.62 | 0.65 | 2.67 | 96% | OK |
| 1 processo, 8 worker(s) | 8 | 6.87 | 0.43 | 290.9 | 3525 | 6549 | 6784 | 2.67 | 0.33 | 2.72 | 98% | OK |
| 1 processo, 16 worker(s) | 16 | 6.59 | 1.25 | 303.3 | 3497 | 6291 | 6513 | 2.78 | 0.17 | 2.83 | 98% | OK |
| 2 processos × 4 workers | 8 | 4.78 | 0.42 | 418.1 | 2413 | 4503 | 4664 | 3.84 | 0.48 | 3.90 | 193% | OK |
| 4 processos × 4 workers | 16 | 3.80 | 0.41 | 526.5 | 1910 | 3519 | 3660 | 4.83 | 0.30 | 4.91 | 361% | OK |

## Controle negativo: com lock x sem lock (alta contenção)

| Workers | Modo | Execuções com venda a mais | Unidades vendidas a mais (média ± dp) | Máximo | Divergência contábil média (un.) | Invariantes |
|--:|---|--:|--:|--:|--:|:-:|
| 1 | COM lock (FOR UPDATE) | 0/5 | 0.0 ± 0.0 | 0 | 0.0 | OK |
| 1 | SEM lock | 0/5 | 0.0 ± 0.0 | 0 | 0.0 | OK |
| 4 | COM lock (FOR UPDATE) | 0/5 | 0.0 ± 0.0 | 0 | 0.0 | OK |
| 4 | SEM lock | 5/5 | 2394.0 ± 0.0 | 2394 | 2991.6 | FALHOU em 5/5 |
| 16 | COM lock (FOR UPDATE) | 0/5 | 0.0 ± 0.0 | 0 | 0.0 | OK |
| 16 | SEM lock | 5/5 | 2394.0 ± 0.0 | 2394 | 5572.4 | FALHOU em 5/5 |

## Gráficos

![grafico_throughput](grafico_throughput.png)
![grafico_speedup](grafico_speedup.png)
![grafico_latencia_p95](grafico_latencia_p95.png)
![grafico_controle_lock](grafico_controle_lock.png)
![grafico_eficiencia](grafico_eficiencia.png)
