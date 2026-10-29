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
```powershell
docker compose up -d
```

### 2. Configure o `.env`
```powershell
Copy-Item .env.example .env
```
Edite o `.env` (ele está no `.gitignore`):

| Variável | Para que serve | Padrão (só dev) |
|---|---|---|
| `DATABASE_URL` | conexão com o PostgreSQL | usuário/senha do `docker-compose.yml` |
| `SECRET_KEY` | chave que assina os JWT (gere uma: `python -c "import secrets; print(secrets.token_urlsafe(48))"`) | chave fixa de desenvolvimento |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | validade do token | `480` (8 h) |
| `ADMIN_NOME`, `ADMIN_EMAIL`, `ADMIN_SENHA` | gestor criado na 1ª execução | `admin@estoque.local` / `admin12345` |

### 3. Instale as dependências
```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 4. Suba a API
```powershell
uvicorn app.main:app --reload
```
Sobe também os **3 workers** da fila (`N_WORKERS` em `app/main.py`).

- **Frontend:** http://localhost:8000/
- **Swagger:** http://localhost:8000/docs (botão **Authorize**: `username` = e-mail, `password` = senha)

### Primeiro acesso
Se a tabela de usuários estiver vazia, a API cria um **gestor** com as credenciais
`ADMIN_EMAIL` / `ADMIN_SENHA` do `.env` e avisa no log para trocar a senha. Entre com ele em
http://localhost:8000/, abra **Usuários** e cadastre almoxarifes e engenheiros
(engenheiro precisa de uma obra). Para trocar a senha do gestor, use "Redefinir senha" na mesma tela.

### Perfis e permissões
Validadas no backend (401 sem token, 403 sem permissão); o frontend só esconde o que o perfil não pode usar.

| Recurso | gestor | almoxarife | engenheiro |
|---|:-:|:-:|:-:|
| Usuários (criar/listar/editar/redefinir senha) | ✅ | ❌ | ❌ |
| Obras: listar | todas | todas | só a sua |
| Obras: criar / andamento / remover | ✅ | ❌ | ❌ |
| Materiais: listar | ✅ | ✅ | ✅ (leitura) |
| Materiais: criar / editar / ajustar estoque | ✅ | ✅ | ❌ |
| Materiais: remover | ✅ | ❌ | ❌ |
| Requisições: ver | todas | todas | só da sua obra |
| Requisições: criar | qualquer obra | ❌ | só a sua obra |
| Dashboard | geral | geral | só a sua obra |
| Diagnóstico de déficit (global) | ✅ | ✅ | ❌ |
| Movimentações: ver / registrar entrada e ajuste | ✅ | ✅ | ❌ |
| Relatórios: consumo, período, taxa de atendimento | todos | todos | só a sua obra |
| Relatórios: estoque crítico | ✅ | ✅ | ✅ |
| Relatório de desempenho do processamento | ✅ | ✅ | ❌ |
| Auditoria | ✅ | ❌ | ❌ |

Cada requisição guarda o `solicitante_id` (usuário logado) para auditoria.

### Telas e recursos de gestão
| Tela (menu) | Para quê | Perfis |
|---|---|---|
| **Dashboard** | cards, 4 gráficos (Chart.js por CDN), alertas de estoque crítico, seletor de período | todos (engenheiro: só a sua obra) |
| **Diagnóstico de déficit** | simula a distribuição do estoque atual entre os pedidos abertos, com semáforo 🟢🟡🔴 e simulação "e se…?" | gestor, almoxarife |
| **Materiais** | busca, cadastro/edição, atalho "+ Entrada" | todos (edição: gestor/almoxarife) |
| **Movimentações** | histórico (filtros + paginação), registrar entrada e ajuste de inventário | gestor, almoxarife |
| **Requisições** | pedidos com vários materiais, busca e filtros, atualização automática | todos |
| **Relatórios** | consumo, requisições por período, estoque crítico, taxa de atendimento, desempenho — com exportação **CSV** para Excel | todos (escopo por perfil) |
| **Auditoria** | quem fez o quê (login, usuários, estoque, pedidos); nunca grava senhas | gestor |

Endpoints novos: `GET /diagnostico/deficit`, `POST /diagnostico/simular`, `GET /movimentacoes`,
`POST /movimentacoes/entrada`, `POST /movimentacoes/ajuste`, `GET /relatorios/{consumo,requisicoes-por-periodo,estoque-critico,taxa-atendimento,desempenho}`
(`?formato=csv` exporta), `GET /dashboard/completo`, `GET /auditoria`. Detalhes, regras de cálculo, a regra do semáforo e as limitações do
diagnóstico estão em [`docs/relatorios-e-diagnostico.md`](docs/relatorios-e-diagnostico.md).

### Dados de demonstração (para vídeos/apresentação)
Dados **fictícios** (25+ materiais elétricos, 3 obras, ~30 dias de pedidos e movimentações, pedidos abertos para o diagnóstico mostrar 🟢🟡🔴):
```powershell
python -m scripts.seed_demo            # idempotente: se a demo já existe, não faz nada
python -m scripts.seed_demo --reset    # APAGA TODO o banco apontado por DATABASE_URL e recria a demo (pede confirmação)
$env:N_WORKERS = "0"; uvicorn app.main:app   # mantém os pedidos abertos para o Diagnóstico (sem workers)
```
Usuários de demonstração (senha de todos: `demo12345`): `gestor@demo.local`, `almoxarife@demo.local`,
`eng.recife@demo.local`, `eng.jaboatao@demo.local`, `eng.olinda@demo.local`.
Roteiro sugerido: com `N_WORKERS=0`, mostre o Diagnóstico (🔴 Cabo 10 mm²: estoque 100 m, demanda 120 m, déficit 20 m); depois reinicie a API
com **`N_WORKERS=1`** e veja os pedidos serem processados exatamente como o diagnóstico previu (conferido: 8 de 8 pedidos).
Com `N_WORKERS=3` o resultado pode divergir da previsão quando dois pedidos do mesmo material são processados ao mesmo tempo
(ver a limitação em `docs/relatorios-e-diagnostico.md` e o achado 14 em `docs/achados.md`).

### Testes automatizados e benchmark

Rodam em bancos **separados** do de desenvolvimento, no mesmo PostgreSQL do `docker-compose`:
`estoque_test` (testes) e `estoque_bench` (benchmark). Eles são **criados automaticamente** (a ferramenta
conecta no banco `postgres` com isolamento AUTOCOMMIT e faz `CREATE DATABASE` se não existir) e o schema do
`estoque_test` é recriado a cada execução. Por segurança, `devtools/dbutil.py` recusa qualquer banco cujo
nome não termine em `_test` ou `_bench`, então o `estoque_db` nunca é tocado.

| Variável | Padrão |
|---|---|
| `TEST_DATABASE_URL` | `postgresql+asyncpg://estoque_user:estoque_pass@localhost:5432/estoque_test` |
| `BENCH_DATABASE_URL` | `postgresql+asyncpg://estoque_user:estoque_pass@localhost:5432/estoque_bench` |
| `N_WORKERS` | `3` (número de workers da API; ver `app/main.py`) |

```powershell
docker compose up -d                       # o Postgres precisa estar no ar
.\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt        # pytest, pytest-asyncio, matplotlib

pytest                                     # tudo (~35 s)
pytest -m "not stress"                     # pula o teste de estresse (200 requisições simultâneas)
pytest tests/test_reprocessamento.py --runxfail   # mostra os números dos ACHADOS (docs/achados.md)
```

O que os testes cobrem: regras de alocação (aprovada/parcial/negada), o cenário do enunciado (100 m, 40/30/50
simultâneos), estresse com 200 requisições disputando um material, isolamento entre materiais, ordem da
fila de prioridade, priorização de ponta a ponta com 1 worker, reprocessamento e reinício, validação de
entradas (404/422/409), login/401/403 por perfil e um fluxo completo pela API com workers reais.
Os testes `xfail` documentam os **achados 1** (reprocessamento duplica o débito) e **14** (com vários workers a prioridade pode
inverter na alocação): são falhas conhecidas, não regressões.

#### Benchmark de concorrência
```powershell
python -m benchmarks.bench_concorrencia --rapido     # versão curta (~2 min)
python -m benchmarks.bench_concorrencia              # completa (~30-40 min)
python -m benchmarks.bench_concorrencia --requisicoes 1000 --workers 1,2,4,8,16,32 --execucoes 3
python -m benchmarks.relatorio                       # regenera resumo.md e gráficos a partir do CSV
```
Parâmetros: `--requisicoes` (2000), `--materiais` (50), `--workers` (1,2,4,8,16), `--execucoes` (5, mais 1 de
aquecimento descartada), `--processos` (2,4: modo multiprocesso), `--workers-controle` (1,4,16),
`--sem-controle`, `--semente`, `--saida`, `--rapido`.

O benchmark **não usa a fila global**: cria a sua `FilaRequisicoes` e inicia N `worker_loop` reais. Mede alta
contenção (1 material) e baixa contenção (50 materiais), um baseline sequencial, o modo multiprocesso e um
**controle negativo** (processamento sem `FOR UPDATE`, só em `benchmarks/sem_lock.py`) que mostra a condição
de corrida. Resultados em `benchmarks/resultados/` (`resultados.csv`, `resumo.md`, `ambiente.json`, gráficos
`grafico_*.png`; a versão `--rapido` fica em `resultados/rapido/`). A análise completa está em
[`docs/benchmark.md`](docs/benchmark.md) e os bugs/fragilidades encontrados em [`docs/achados.md`](docs/achados.md).

### Atualizando um banco já existente (sem Alembic)
`create_all` **não altera tabelas existentes**. Ao subir a API, `init_models()` cria o que é novo (`usuarios`,
`movimentacoes_estoque`, `auditoria`) e aplica `ALTER TABLE … ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` nas
tabelas antigas (colunas e índices de `requisicoes`), então **um banco de desenvolvimento já existente continua funcionando
sem apagar nada**. Atenção: o histórico antigo **não tem movimentações** (o livro-razão começa a partir de agora); os materiais
já cadastrados só terão `ENTRADA` de saldo inicial se forem recriados.

Para começar do zero, com o livro-razão completo **(apaga TODOS os dados locais — confirme antes)**:
```powershell
docker compose down -v      # remove o contêiner E o volume do Postgres (todos os dados)
docker compose up -d
uvicorn app.main:app --reload   # recria as tabelas e o gestor inicial do .env
python -m scripts.seed_demo     # (opcional) popula os dados de demonstração
```
Alternativa sem destruir o volume inteiro: `python -m scripts.seed_demo --reset` limpa **apenas o banco apontado por `DATABASE_URL`**
e pede confirmação.

### 5. Rode a demonstração do cenário de concorrência
Em outro terminal, com a API rodando (faz login com o gestor do `.env`):
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
├── models.py                 # Material, Obra, Requisicao, Usuario (SQLAlchemy)
├── schemas.py                 # validação de entrada/saída (Pydantic)
├── core/
│   ├── database.py            # engine assíncrona + sessão do banco
│   ├── config.py              # variáveis do .env
│   ├── security.py            # bcrypt + JWT
│   └── auth.py                # get_current_user, exigir_perfis
├── services/
│   ├── estoque_service.py     # <- lógica crítica de lock/consistência (+ saída no livro-razão)
│   ├── fila_service.py        # fila de prioridade + pool de workers
│   ├── prioridade.py          # chave de prioridade (compartilhada pela fila e pelo diagnóstico)
│   ├── diagnostico_service.py # simulação do déficit + semáforo
│   ├── auditoria_service.py   # registro de auditoria (sem segredos)
│   └── csv_export.py          # CSV compatível com Excel pt-BR
└── api/
    ├── routes.py               # materiais, obras, requisições, dashboard resumido
    ├── auth_routes.py          # /auth e /usuarios
    ├── estoque_routes.py       # /movimentacoes e /auditoria
    ├── diagnostico_routes.py   # /diagnostico (déficit e simulação)
    └── relatorios.py           # /relatorios/* (+CSV) e /dashboard/completo
frontend/                        # HTML+CSS+JS puros, servidos pela API (/, /login, /static)
├── index.html, login.html
├── css/style.css
└── js/ api.js, app.js, login.js, pages/{dashboard,materiais,obras,requisicoes,usuarios}.js
scripts/seed_demo.py             # dados fictícios de demonstração (idempotente, --reset)
tests/                           # pytest no Postgres: regras, concorrência, fila, API, permissões, diagnóstico, relatórios
benchmarks/                      # benchmark de concorrência + controle negativo + relatório
devtools/dbutil.py               # cria/limpa os bancos descartáveis (_test, _bench)
docs/                            # benchmark.md (metodologia e resultados) e achados.md
demo_concorrencia.py            # script que reproduz o cenário de disputa
docker-compose.yml               # sobe o Postgres local
```

## Próximos passos sugeridos (bons temas para a monografia)

- [ ] Métricas: tempo médio de fila, throughput por worker, contenção de lock
- [ ] Testes de carga simulando N obras x M materiais concorrentes
- [ ] Trocar a fila em memória por Redis Streams/RabbitMQ e comparar
- [ ] Endpoint de "diagnóstico de déficit" (o que o enunciado descreveu:
      "Cabo insuficiente, déficit de 20m, Obra C pendente")
- [x] Autenticação/autorização por perfil e obra
- [ ] Alembic para migrações versionadas do schema
