# Controle de Estoque Paralelo — Engenharia Elétrica

MVP de um sistema de controle de estoque com **processamento concorrente
de requisições**, feito para uma empresa fictícia de engenharia elétrica
com várias obras simultâneas (Recife, Jaboatão, Olinda...).

## O problema que o sistema resolve

Várias equipes podem pedir o mesmo material ao mesmo tempo. Sem controle
de concorrência, isso permite que o sistema "libere" mais material do que
existe em estoque (race condition clássica). Este projeto resolve isso com
**lock pessimista no banco** (`SELECT ... FOR UPDATE`), não com lock na
aplicação — o que garante a integridade mesmo se você escalar para vários
processos/instâncias da API no futuro.

## Arquitetura

```
Cliente ──POST /requisicoes──▶ API (FastAPI)
                                   │
                                   ▼
                         cria Requisicao (PENDENTE)
                                   │
                                   ▼
                     Fila de prioridade (em memória, asyncio)
                                   │
                ┌──────────────────┼──────────────────┐
                ▼                  ▼                  ▼
           worker-1           worker-2           worker-3
                │                  │                  │
                └──────────────────┼──────────────────┘
                                   ▼
                    processar_requisicao()
                    SELECT ... FOR UPDATE no Material
                    (trava a linha até o commit)
                                   ▼
                              PostgreSQL
```

**Por que a fila não é o que garante consistência:** a fila só organiza
a *ordem* de processamento e a prioridade. A garantia real vem da
transação com `FOR UPDATE` em `app/services/estoque_service.py` — é ela
que impede dois workers de debitarem o mesmo estoque ao mesmo tempo.

### Critérios de priorização (em `fila_service.py`)
1. Obra parada por falta de material
2. Nível de prioridade da obra (1 = mais urgente)
3. Prazo de entrega mais próximo
4. Requisição mais antiga (desempate)

## Como rodar

### 1. Suba o PostgreSQL
```bash
docker compose up -d
```

### 2. Instale as dependências
```bash
pip install -r requirements.txt
```

### 3. Suba a API
```bash
uvicorn app.main:app --reload
```
Isso já sobe **3 workers** consumindo a fila em background (definido em
`app/main.py`, variável `N_WORKERS`).

- **Front-end (painel visual):** http://localhost:8000/
- Documentação interativa da API: http://localhost:8000/docs

### 4. Rode a demonstração do cenário de concorrência
Em outro terminal, com a API rodando:
```bash
python demo_concorrencia.py
```
Isso simula exatamente o cenário do enunciado: 100m de cabo em estoque,
3 obras pedindo 40m/30m/50m ao mesmo tempo (120m total). Você vai ver
no output quem foi atendido integralmente, quem ficou parcial, e que o
estoque nunca fica negativo.

## Estrutura do projeto

```
app/
├── main.py                  # entrada da aplicação, sobe os workers
├── models.py                 # Material, Obra, Requisicao (SQLAlchemy)
├── schemas.py                 # validação de entrada/saída (Pydantic)
├── core/
│   └── database.py            # engine assíncrona + sessão do banco
├── services/
│   ├── estoque_service.py     # <- lógica crítica de lock/consistência
│   └── fila_service.py        # fila de prioridade + pool de workers
└── api/
    └── routes.py               # endpoints REST
frontend/index.html              # painel web (servido pela própria API em /)
demo_concorrencia.py            # script que reproduz o cenário de disputa
docker-compose.yml               # sobe o Postgres local
```

## Próximos passos sugeridos (bons temas para a monografia)

- [ ] Métricas: tempo médio de fila, throughput por worker, contenção de lock
- [ ] Testes de carga simulando N obras x M materiais concorrentes
- [ ] Trocar a fila em memória por Redis Streams/RabbitMQ e comparar
- [ ] Endpoint de "diagnóstico de déficit" (o que o enunciado descreveu:
      "Cabo insuficiente, déficit de 20m, Obra C pendente")
- [ ] Autenticação/autorização por equipe/obra
- [ ] Alembic para migrações versionadas do schema
