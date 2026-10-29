# Resultados do benchmark (gerado automaticamente)

## Ambiente

- **data**: 2026-10-28 23:49:24
- **python**: 3.13.12
- **so**: Windows-11-10.0.26200-SP0
- **processador**: AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD
- **cpus_logicas**: 16
- **postgresql**: PostgreSQL 16.15 on x86_64-pc-linux-musl
- **postgres_em_docker**: sim (estoque_postgres|postgres:16-alpine|0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp)
- **pool_size**: 20
- **max_overflow**: 10
- **banco**: estoque_bench
- **parâmetros**: 1000 requisições, 50 materiais (baixa contenção), 5 execuções medidas + 1 de aquecimento, semente 42

## Comparação de versões (A/B intercalado): antes x depois do livro-razão

| Cenário | Workers | Anterior (req/s) | Atual (req/s) | Variação | Tempo anterior (s) | Tempo atual (s) | CPU anterior | CPU atual | Atual sem índices novos (req/s) | Variação só do INSERT |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| Alta contenção | 4 | 239 ± 107 | 211 ± 20 | -11.7% | 4.19 | 4.75 | 81% | 81% | 219 ± 3 | -8.0% |
| Alta contenção | 16 | 233 ± 4 | 212 ± 5 | -8.8% | 4.30 | 4.71 | 80% | 77% | 213 ± 38 | -8.7% |
| Baixa contenção | 4 | 298 ± 26 | 280 ± 6 | -6.0% | 3.35 | 3.57 | 96% | 96% | 268 ± 12 | -10.2% |
| Baixa contenção | 16 | 312 ± 6 | 280 ± 24 | -10.5% | 3.20 | 3.57 | 98% | 98% | 287 ± 8 | -8.2% |

## Gráficos

![grafico_versoes](grafico_versoes.png)
