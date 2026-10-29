# Achados (bugs e fragilidades) — com correção proposta

Encontrados durante a escrita dos testes e do benchmark. **Nenhuma correção foi aplicada**: a lógica de
produção (`estoque_service`, `fila_service`, modelos) permanece como estava. Cada item cita o teste ou
a medição que o evidencia.

## Resumo

| # | Achado | Severidade | Evidência |
|---|---|---|---|
| 14 | **Com mais de 1 worker a prioridade não vale na alocação do estoque**: a fila retira em ordem, mas os itens correm juntos e o lock do material vai para quem chegar primeiro (inversões medidas de 0 % a 35 % conforme as condições, com 3 ou 8 workers; sempre 0 % com 1) | **Alta** (é o requisito central de priorização) | `tests/test_prioridade_concorrente.py` (xfail) e experimento em `docs/relatorios-e-diagnostico.md` |
| 1 | `processar_requisicao` não é idempotente: reprocessar a mesma requisição debita o estoque de novo | **Alta** | `tests/test_reprocessamento.py` (2 testes `xfail`) |
| 2 | Com mais de um processo da API (`uvicorn --workers N`), cada processo tem sua própria fila e reenfileira *todas* as pendentes na partida → duplicidade → dispara o achado 1 | **Alta** (se escalar horizontalmente) | análise de `main.py`/`fila_service.py`; teste de reinício |
| 3 | Requisições em `ERRO` nunca são reprocessadas; falhas transitórias (ex.: perda de conexão) viram erro definitivo | Média | análise de `worker_loop`/`_marcar_erro` |
| 4 | A API grava a requisição e só depois a coloca na fila: entre os dois passos a requisição fica sem dono até a próxima reinicialização | Baixa | análise de `routes.py` |
| 5 | Mais workers que conexões do pool (`pool_size=20 + max_overflow=10 = 30`) não dá ganho, e o excedente só espera conexão | Baixa | análise do código (32 workers **não foi medido**; o benchmark mede até 16) |
| 6 | A fila é em memória e não limitada: sem *back-pressure* e perdida em queda do processo (mitigado só por `reenfileirar_pendentes`) | Informativa | análise |
| 7 | A chave de prioridade da fila é calculada **ao enfileirar** e não muda se a obra mudar de prioridade/“parada” depois; o diagnóstico usa os dados **atuais** da obra | Média | análise (`prioridade.py`/`diagnostico_service.py`) |
| 8 | Quantidades de estoque são `Float`: somas longas acumulam erro de arredondamento (a conservação só é exata com valores “bonitos”) | Média | análise; os testes de conservação usam inteiros |
| 9 | “Por dia” nos relatórios e no dashboard agrupa por dia **UTC**; em Recife (UTC−3) pedidos feitos à noite caem no dia seguinte | Baixa | análise |
| 10 | `PUT /materiais/{id}` ainda aceita mudar `quantidade_estoque` (vira `AJUSTE` com motivo genérico), contornando o “motivo obrigatório” | Baixa | `test_edicao_de_quantidade_vira_ajuste` |
| 11 | Remover material apaga o histórico de movimentações dele; remover obra com histórico apaga as requisições (as saídas ficam, sem vínculo) | Média | `test_remover_*` |
| 12 | Falhas de login são auditadas **sem autenticação**: quem insistir gera linhas sem limite (crescimento de tabela / ruído), e não há *rate limit* | Média | análise (`/auth/login`) |
| 13 | O livro-razão custou ≈ 4–13 % de throughput (o `INSERT` extra dentro do lock); dá para fundir débito e registro em 1 comando SQL | Informativa | A/B em `docs/benchmark.md` §7 |

## 1. Reprocessamento duplica o débito (ALTA)

**O que acontece.** `processar_requisicao` lê a requisição, não verifica o status e sempre debita. Estoque
100, requisição de 30, processada duas vezes:

```
estoque esperado: 70      estoque real: 40
```

Acontece tanto em chamadas sequenciais quanto simultâneas (dois workers com o mesmo id): ambos leem a
requisição como pendente e ambos debitam. O `FOR UPDATE` protege a linha do **material**, mas a linha da
**requisição** não é travada nem conferida.

**Quando pode ocorrer na prática.** O mesmo id entrar duas vezes na fila (achado 2), ou uma futura rotina
de "reprocessar erros" (achado 3) sem a proteção.

**Correção proposta** (em `estoque_service.py`, mesmo padrão de lock já usado): travar a requisição e
ignorar quem já foi resolvida.

```python
async with db.begin():
    requisicao = (await db.execute(
        select(Requisicao).where(Requisicao.id == requisicao_id).with_for_update()
    )).scalar_one_or_none()
    if requisicao is None:
        raise ValueError(f"Requisição {requisicao_id} não encontrada")
    if requisicao.status in (StatusRequisicao.APROVADA, StatusRequisicao.PARCIAL, StatusRequisicao.NEGADA):
        return requisicao          # já resolvida: idempotente, não debita de novo
    ...
```

Lock sempre na ordem requisição → material (todos os workers seguem a mesma ordem, sem risco de deadlock).
Ao aplicar, remover os `@pytest.mark.xfail(strict=True)` dos dois testes (o `strict` avisa com XPASS).

## 2. Vários processos da API reenfileiram tudo (ALTA, se escalar)

`lifespan` chama `reenfileirar_pendentes()` em **todo** processo que sobe. Com `uvicorn --workers 2`, cada
processo enfileira *todas* as requisições abertas do banco (inclusive as que o outro processo já está
processando). Sem o achado 1 corrigido, isso duplica débitos; com ele corrigido, ainda desperdiça trabalho.
O benchmark multiprocesso (`docs/benchmark.md`) usa filas separadas por processo, mas com **partições
disjuntas** de requisições — que é o que a arquitetura precisaria garantir.

**Correção proposta.** Reivindicar o trabalho no banco em vez de na memória: o worker pega a próxima
requisição pendente com `SELECT ... FOR UPDATE SKIP LOCKED` (fila no Postgres), ou usa uma fila externa
(Redis/RabbitMQ — já listado nos próximos passos do README). Alternativa mínima: reenfileirar só na
partida e só a requisição ainda `PENDENTE` com `UPDATE ... SET status='em_processamento' WHERE status='pendente'
RETURNING id` (reivindicação atômica).

## 3. Requisições em `ERRO` são definitivas

`worker_loop` captura qualquer exceção e marca `ERRO`; nada reprocessa. Um erro transitório (rede, timeout
de pool, deadlock) vira falha permanente sem notificação. **Correção proposta:** classificar erros
(transitório → recolocar na fila com *retry* limitado e *backoff*; permanente → `ERRO`) — dependendo do
achado 1 estar corrigido.

## 4. Janela entre `commit` e `fila.adicionar`

`criar_requisicao`/`criar_pedido` fazem `commit` e só depois enfileiram. Se o processo cair entre os dois
passos, a requisição fica `PENDENTE` sem estar na fila até alguém reiniciar a API (ou nunca, se a API
não reiniciar). **Correção proposta:** um *scanner* periódico que reenfileira `PENDENTE` antigas (com a
reivindicação atômica do achado 2) — o padrão "outbox".

## 5. Workers além do pool de conexões

O engine tem `pool_size=20, max_overflow=10` (30 conexões). Cada worker usa uma conexão por vez, então até
30 workers rodam sem esperar; acima disso o excedente aguarda no pool (timeout padrão de 30 s do
SQLAlchemy). Não há *deadlock* (nenhum worker precisa de duas conexões ao mesmo tempo, salvo `_marcar_erro`
em caso de exceção) — só fila. Além disso, o benchmark mostra que, com 1 processo, o ganho já termina em ~4–8 workers (ver
`docs/benchmark.md`); acima do pool não foi medido. **Recomendação:** manter `N_WORKERS` ≤ ~8 por processo e documentar que `N_WORKERS`
acima de `pool_size + max_overflow` não ajuda.

## 6. Fila em memória, sem limite

Sem limite de tamanho, uma rajada grande só cresce na memória (sem *back-pressure* para o cliente), e a
prioridade só vale dentro de um processo (achado 2). Registrado como limitação conhecida e tema de
monografia; não é bug.

## Observações menores

- O benchmark mostra que `SELECT ... FOR UPDATE` não é o gargalo no cenário de **baixa** contenção; veja
  a análise em `docs/benchmark.md`.
- `processar_requisicao` faz várias idas ao banco por requisição (leitura da requisição, atualização de
  status, `SELECT ... FOR UPDATE` do material, gravações, `COMMIT` e `refresh`). Reduzir idas (por exemplo
  com um `UPDATE ... RETURNING` atômico) aumentaria o teto de throughput — candidato a "otimização
  demonstrável" futura, a ser medida com este mesmo benchmark.

## 7. Chave de prioridade congelada na fila (média)
`FilaRequisicoes.adicionar` calcula a chave **uma vez**, com os dados da obra naquele instante. Se um gestor marcar a obra como
“parada por falta de material” (ou mudar o nível de prioridade) com pedidos já na fila, esses pedidos continuam com a chave
antiga; só os pedidos novos usam a regra atualizada. O diagnóstico de déficit lê a obra **agora**, então pode divergir do que a fila
vai fazer de fato. **Correção proposta:** reordenar a fila quando a obra mudar (reconstruir o heap) ou fazer o worker
reavaliar a prioridade no momento de retirar o item.

## 8. Estoque em `Float` (média)
`quantidade_estoque`, `quantidade_solicitada`, `quantidade_atendida` e `saldo_apos` são `Float`. Com quantidades fracionárias
(por exemplo 0,1 m) a conservação “inicial + entradas − saídas == atual” deixa de ser exata. **Correção proposta:** `Numeric(12, 3)`
com `Decimal` na camada de serviço (mudança de modelo ⇒ migração; deixada para quando houver Alembic).

## 9. Dia em UTC nos relatórios (baixa)
Os timestamps são UTC naive, e os agrupamentos “por dia/semana” também. Para a equipe de Recife, um pedido às 22h local aparece
no dia seguinte. **Correção proposta:** agrupar em SQL por
`date_trunc('day', criado_em AT TIME ZONE 'UTC' AT TIME ZONE 'America/Recife')`.
*(Durante esta etapa um teste pegou um bug do próprio dashboard, que misturava `date.today()` local com datas UTC; já corrigido.)*

## 10–12. Pequenas fragilidades da etapa de relatórios
* **#10** Remover `quantidade_estoque` do `PUT /materiais` e obrigar Entrada/Ajuste (a tela já faz isso; só a API ficou compatível).
* **#11** Trocar a remoção por **arquivamento** (`ativo=false`) de materiais e obras, preservando histórico e auditoria.
* **#12** *Rate limit* no login (por IP e por e-mail) e política de retenção/rotação da tabela `auditoria`.
* O dashboard carrega o Chart.js por **CDN sem SRI**: sem internet os gráficos não aparecem (a tela avisa e os cards continuam
  funcionando). Para demonstrações offline, baixar o arquivo para `frontend/vendor/`.

## 13. Custo medido do livro-razão e otimização candidata (informativa)
O registro da SAÍDA na mesma transação do débito custou **≈ 4–13 % de throughput** (A/B intercalado em `docs/benchmark.md`, seção 7),
quase todo do `INSERT` extra; na alta contenção ele fica dentro da seção crítica do lock. **Otimização proposta (não aplicada):** um único comando
SQL que debita e registra a movimentação (`WITH novo AS (UPDATE materiais … RETURNING …) INSERT INTO movimentacoes_estoque …`),
removendo uma ida ao banco por pedido; deve ser feita com cuidado (é a lógica crítica) e medida com o mesmo A/B.

## 14. Inversão de prioridade com vários workers (ALTA)
**O que acontece.** `FilaRequisicoes` entrega os itens em ordem de prioridade, mas cada worker, ao retirar um item, segue sozinho para
o banco. Com N > 1 workers, dois pedidos do **mesmo material** passam a correr ao mesmo tempo, e quem obtém o `SELECT … FOR UPDATE` primeiro
leva o estoque, mesmo que tenha sido retirado depois (diferenças de agendamento do asyncio, de conexão disponível no pool, de latência). O lock
garante **consistência** do saldo, mas não a **ordem** de atendimento.

**Medição** (obras disputando 10 unidades, que só atendem a obra mais prioritária — uma "parada por falta de material"). Como é uma **corrida**,
a frequência depende das condições; as medições abaixo foram feitas em momentos e cenários diferentes:

| Condição | A mais prioritária levou o estoque | Inversões |
|---|--:|--:|
| 1 worker (pool quente ou frio) | 60/60 e 30/30 | **0 %** |
| 3 workers, pool quente, com outro tráfego (sondagem a cada 10 ms) | 39 / 60 | **35 %** |
| 8 workers (8 obras), idem | 39 / 60 | **35 %** |
| 3 workers, pool **frio** (como logo após reiniciar a API) | 25 / 30 | **17 %** |
| 3 workers, pool quente, sondagem a cada 50 ms | 28 / 30 | **7 %** |
| 3 workers dentro do pytest (pool quente dedicado) | 25 / 25 | 0 % |

Ou seja: **de 0 % a 35 %**, e **sempre 0 % com 1 worker**. Não dá para cravar um número; o que se pode afirmar é que **nada no desenho impede a inversão**.

Observado também na demonstração (logo após reiniciar a API, com 3 workers): a obra **parada** (prioridade máxima) recebeu 1 de 3 *quadros de distribuição*, enquanto uma obra de
menor prioridade recebeu os 4 que pediu. O teste de priorização de ponta a ponta (`tests/test_fila_prioridade.py`) usa **1 worker**, por isso não detectava isso.
`tests/test_prioridade_concorrente.py` reproduz o problema **de forma determinística**: atrasa a abertura da conexão do worker que retirou o
item mais prioritário (o que uma conexão fria faz de verdade) e mostra que outro worker leva o estoque.

**Correção proposta (não aplicada).** Garantir a ordem **por material** sem perder o paralelismo entre materiais:
* **Senhas por material:** ao retirar o item, *dentro do mesmo lock da fila*, registrar uma "senha" em uma fila FIFO do material dele; o worker só
  vai ao banco quando a senha anterior daquele material terminar. A ordem das senhas é a ordem de retirada (= prioridade), e materiais diferentes
  continuam em paralelo; **ou**
* **Partição por material:** cada worker atende só os materiais com `material_id % N == i` (fila por worker), preservando a prioridade dentro de cada material,
  ao custo de pior balanceamento quando um material concentra a carga.

Ao aplicar, remover o `xfail` de `tests/test_prioridade_concorrente.py` (o `strict` avisa com XPASS) e reexecutar o benchmark, porque serializar por material pode
reduzir o throughput na alta contenção (que já é limitada pela linha, ver `docs/benchmark.md`).
