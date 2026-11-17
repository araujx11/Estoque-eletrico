# Achados (bugs e fragilidades) — com correção proposta

Encontrados durante a escrita dos testes e do benchmark. Cada item cita o teste ou a medição que o evidencia.

**Atualização da Sprint 05:** os achados **1** (idempotência) e **14** (prioridade com vários workers) e o novo achado **15**
(mensagens de validação em inglês) foram **corrigidos**, com testes e medições antes/depois (ver "Correção aplicada" em cada um).
O achado 2 ficou **mitigado** pela correção do 1. Os demais continuam sem correção.

**Atualização (pendências 3 e 4):** os achados **2** (fila com vários processos), **3** (erros nunca reprocessados) e **8** (`Float`) foram **corrigidos**, e a correção do 3 revelou e corrigiu o novo achado **16** (falha depois do commit virava `ERRO`). Limitações que sobraram estão no achado **17**.

## Resumo

| # | Achado | Severidade | Evidência |
|---|---|---|---|
| 14 | ✅ **CORRIGIDO na Sprint 05.** **Com mais de 1 worker a prioridade não vale na alocação do estoque**: a fila retira em ordem, mas os itens correm juntos e o lock do material vai para quem chegar primeiro (inversões medidas de 0 % a 35 % conforme as condições, com 3 ou 8 workers; sempre 0 % com 1) | **Alta** (é o requisito central de priorização) | `tests/test_prioridade_concorrente.py` (xfail) e experimento em `docs/relatorios-e-diagnostico.md` |
| 1 | ✅ **CORRIGIDO na Sprint 05.** `processar_requisicao` não era idempotente: reprocessar a mesma requisição debita o estoque de novo | **Alta** | `tests/test_reprocessamento.py` (2 testes `xfail`) |
| 2 | ✅ **CORRIGIDO** (reivindicação atômica com `FOR UPDATE SKIP LOCKED`; antes só mitigado pela correção do 1). Com mais de um processo da API (`uvicorn --workers N`), cada processo tem sua própria fila e reenfileira *todas* as pendentes na partida → duplicidade → dispara o achado 1 | **Alta** (se escalar horizontalmente) | `tests/test_fila_erros.py` (3 instâncias sobre o mesmo banco) |
| 3 | ✅ **CORRIGIDO.** Requisições em `ERRO` nunca eram reprocessadas; falhas transitórias (ex.: perda de conexão) viravam erro definitivo | Média | `tests/test_fila_erros.py` (erro transitório, persistente, backoff) |
| 4 | A API grava a requisição e só depois a coloca na fila: entre os dois passos a requisição fica sem dono até a próxima reinicialização | Baixa | análise de `routes.py` |
| 5 | Mais workers que conexões do pool (`pool_size=20 + max_overflow=10 = 30`) não dá ganho, e o excedente só espera conexão | Baixa | análise do código (32 workers **não foi medido**; o benchmark mede até 16) |
| 6 | A fila é em memória e não limitada: sem *back-pressure* e perdida em queda do processo (mitigado só por `reenfileirar_pendentes`) | Informativa | análise |
| 7 | A chave de prioridade da fila é calculada **ao enfileirar** e não muda se a obra mudar de prioridade/“parada” depois; o diagnóstico usa os dados **atuais** da obra | Média | análise (`prioridade.py`/`diagnostico_service.py`) |
| 8 | ✅ **CORRIGIDO.** Quantidades de estoque eram `Float`: somas longas acumulavam erro de arredondamento (0,1 somado 10 mil vezes dava 1000,0000000001588) | Média | `tests/test_decimal.py` |
| 9 | “Por dia” nos relatórios e no dashboard agrupa por dia **UTC**; em Recife (UTC−3) pedidos feitos à noite caem no dia seguinte | Baixa | análise |
| 10 | `PUT /materiais/{id}` ainda aceita mudar `quantidade_estoque` (vira `AJUSTE` com motivo genérico), contornando o “motivo obrigatório” | Baixa | `test_edicao_de_quantidade_vira_ajuste` |
| 11 | Remover material apaga o histórico de movimentações dele; remover obra com histórico apaga as requisições (as saídas ficam, sem vínculo) | Média | `test_remover_*` |
| 12 | Falhas de login são auditadas **sem autenticação**: quem insistir gera linhas sem limite (crescimento de tabela / ruído), e não há *rate limit* | Média | análise (`/auth/login`) |
| 15 | ✅ **CORRIGIDO na Sprint 05.** Mensagens de validação (422) em inglês e a resposta **ecoava o valor enviado** (inclusive senhas) | Média | `tests/test_mensagens_erro.py`; conferido na API antes e depois |
| 16 | ✅ **CORRIGIDO junto com o 3.** `_marcar_erro` gravava `ERRO` em qualquer requisição, inclusive numa que **já tinha sido aprovada** (falha de conexão *depois* do commit, no `refresh`). Com o reprocessamento automático isso viraria **débito em dobro** | **Alta** (só passou a importar com o achado 3) | `test_falha_depois_do_commit_nao_vira_erro_nem_debita_de_novo` (falha sem a proteção) |
| 17 | Limitações que sobraram da fila com vários processos: a prioridade só é estrita dentro de um processo; o prazo da reivindicação não é renovado; a partida não distribui a carga | Média | análise + `test_prioridade_dentro_de_cada_instancia_e_global_quando_uma_so_reivindica` |
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

**Correção aplicada (Sprint 05).** `processar_requisicao` agora trava a linha da requisição (`FOR UPDATE`) antes da do material e devolve,
sem debitar, quem já está `APROVADA`, `PARCIAL` ou `NEGADA` (ERRO não é estado final: a transação que falhou foi desfeita). A ordem de
locks é sempre requisição → material (nenhuma outra rota trava material e depois requisição, então não há deadlock). Antes: estoque 40
em vez de 70 nos dois testes; depois: 70. Os dois `xfail` viraram testes normais e há 5 novos (várias repetições, parcial/negada, 10
workers na mesma requisição, requisição em ERRO e o gancho da fila).

## 2. Vários processos da API reenfileiram tudo (ALTA, se escalar) — corrigido

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

**Correção aplicada.** Na partida, cada processo **reivindica** as requisições no banco antes de enfileirá-las
(`fila_service.reivindicar_e_enfileirar`):

```sql
UPDATE requisicoes SET status='EM_PROCESSAMENTO', reivindicada_por=:processo, reivindicada_em=:agora
WHERE id IN (SELECT id FROM requisicoes
             WHERE status='PENDENTE' OR (status='EM_PROCESSAMENTO' AND (reivindicada_em IS NULL OR reivindicada_em < :agora - :lease))
             FOR UPDATE SKIP LOCKED)
RETURNING id, obra_id, material_id, criado_em
```

* **Por que atômica e sem espera.** O `UPDATE` é um comando só: a linha muda de `PENDENTE` para `EM_PROCESSAMENTO` com o dono gravado na
  mesma instrução. `SKIP LOCKED` faz o processo que chegar depois **pular** o que o outro está reivindicando em vez de esperar; assim dois
  processos recebem conjuntos **disjuntos** e nenhuma requisição entra em duas filas. A transação só contém esse `UPDATE` (milissegundos),
  e ela **não** pega o lock do material, então não interfere na ordem de locks requisição → material.
* **Prazo (lease, `FILA_LEASE_S`, padrão 300 s).** Se o processo dono cair, a requisição ficaria presa em `EM_PROCESSAMENTO`. Passado o prazo,
  outro processo a retoma (a rotina periódica do achado 3 também faz essa varredura). Registros antigos sem dono (`reivindicada_em` nulo)
  contam como vencidos.
* **Por que é segura com a idempotência (RN14).** A reivindicação só evita **trabalho duplicado**; a **correção** do estoque não depende dela.
  Mesmo que a mesma requisição acabe em duas filas (prazo vencido enquanto o dono ainda a processava; requisição criada pela API de um
  processo enquanto outro reenfileira), `processar_requisicao` trava a requisição e a encontra já resolvida: devolve sem debitar. Pior caso =
  um processamento a mais que não faz nada.
* **Teste** (`tests/test_fila_erros.py`): 3 instâncias reivindicando ao mesmo tempo (conjuntos disjuntos e completos, a reivindicação aponta
  para quem enfileirou); reivindicada não é reivindicada de novo; prazo vencido permite retomar; `SKIP LOCKED` não espera uma linha travada
  por outra transação (sem ele o teste trava); 3 instâncias com 3 workers cada, 135 requisições com déficit: nenhuma requisição com mais de
  uma saída e estoque conservado **exatamente** em cada material.

## 3. Requisições em `ERRO` são definitivas (corrigido)

`worker_loop` captura qualquer exceção e marca `ERRO`; nada reprocessa. Um erro transitório (rede, timeout
de pool, deadlock) vira falha permanente sem notificação. **Correção proposta:** classificar erros
(transitório → recolocar na fila com *retry* limitado e *backoff*; permanente → `ERRO`) — dependendo do
achado 1 estar corrigido.

**Correção aplicada.** Novos campos em `requisicoes`: `tentativas`, `ultimo_erro`, `proxima_tentativa_em`.

* `_marcar_erro` conta a tentativa, guarda `Tipo: mensagem` (até 500 caracteres) e agenda a próxima com *backoff* exponencial
  (`ERRO_BACKOFF_BASE_S` × 2^(tentativas−1); padrão 5 s, 10 s, 20 s...).
* Rotina periódica (`reprocessar_erros_loop`, a cada `ERRO_REPROCESSAR_INTERVALO_S`, padrão 30 s; `0` desliga) reivindica de forma atômica (a
  mesma do achado 2) as requisições em `ERRO` com `tentativas < ERRO_MAX_TENTATIVAS` (padrão 3) e backoff vencido, e as recoloca na fila.
* Ao atingir o limite a requisição fica em **ERRO definitivo**: deixa de ser candidata, gera o registro `requisicao_erro_definitivo` na
  auditoria e aparece em `GET /diagnostico/deficit` (campo `erros`, com `tentativas`, `max_tentativas`, `definitivo`, `ultimo_erro`).
* Segurança do débito: ERRO significa transação desfeita (nada debitado), e o reprocessamento passa pelo mesmo `processar_requisicao`
  idempotente. A rotina não distingue erro transitório de permanente; o limite de tentativas é o que impede o laço infinito.
* **Testes:** erro transitório é reprocessado pela própria rotina e debita **uma** vez (1 saída); erro persistente para em 3 tentativas, deixa o
  estoque intacto e grava **uma** linha de auditoria; backoff adia a nova tentativa; 40 requisições com falhas transitórias não geram saída duplicada.

## 16. `_marcar_erro` sobrescrevia requisições já resolvidas (ALTA, descoberto ao corrigir o achado 3)
`_marcar_erro` fazia `req.status = ERRO` sem olhar o estado atual. Se a falha acontecesse **depois** do commit do débito (por exemplo, a
conexão cai no `db.refresh` que vem depois), a requisição já estava `APROVADA` e o estoque debitado, mas passava a `ERRO`. Com o reprocessamento
automático ela seria processada de novo e **debitaria duas vezes**. **Correção aplicada:** `_marcar_erro` trava a linha e não altera quem já está
`APROVADA`, `PARCIAL` ou `NEGADA`. Teste `test_falha_depois_do_commit_nao_vira_erro_nem_debita_de_novo`; confirmado que ele **falha** sem a proteção.

## 17. Limitações que sobraram da fila com vários processos (média)
* **Prioridade entre processos não é estrita.** As senhas por material (RN15) e o heap são em memória de cada processo. Se dois processos
  reivindicam ao mesmo tempo, cada um recebe um subconjunto e a ordem **entre** subconjuntos é a da corrida pelo lock do material. Com um único
  processo reivindicando (partida normal: o primeiro pega tudo) a prioridade vale para o conjunto inteiro. **Correção proposta:** fila única no
  banco (ordenada por prioridade) ou broker externo.
* **O prazo da reivindicação não é renovado.** Um backlog muito grande, esperando na fila em memória de quem o reivindicou por mais que
  `FILA_LEASE_S`, pode ser retomado por outro processo. Não há risco para o estoque (RN14), só trabalho repetido. **Correção proposta:** *heartbeat*
  que renova `reivindicada_em`, ou prazo maior.
* **A partida não distribui a carga:** quem sobe primeiro reivindica tudo que está aberto. Os processos só dividem o trabalho do que chega depois
  pela API (cada um enfileira o que recebe).
* Uma requisição criada pela API de um processo ainda está `PENDENTE` quando outro processo reenfileira: pode ser reivindicada por ele (duplicidade
  inofensiva, ver RN14).

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

## 8. Estoque em `Float` (média) — corrigido
`quantidade_estoque`, `quantidade_solicitada`, `quantidade_atendida` e `saldo_apos` são `Float`. Com quantidades fracionárias
(por exemplo 0,1 m) a conservação “inicial + entradas − saídas == atual” deixa de ser exata. **Correção proposta:** `Numeric(12, 3)`
com `Decimal` na camada de serviço (mudança de modelo ⇒ migração; deixada para quando houver Alembic).

**Correção aplicada.** As 6 colunas de quantidade (`materiais.quantidade_estoque/estoque_critico`, `requisicoes.quantidade_solicitada/atendida`,
`movimentacoes_estoque.quantidade/saldo_apos`) são `NUMERIC(14,3)` e o código usa `decimal.Decimal` (modelos, serviço de estoque, diagnóstico,
relatórios, auditoria, CSV).

* **Escala:** 3 casas decimais e até 11 dígitos inteiros (14 no total). Cabe em um `double` sem perda, então a API continua devolvendo **número** no
  JSON (`Qtd` em `app/schemas.py`) e o frontend não mudou (`step="any"`, `fmt` com até 3 casas). A entrada com mais de 3 casas ou mais de 11
  dígitos inteiros é **recusada** com 422 em português (não arredonda em silêncio), sem devolver o valor enviado (RN16).
* **Migração (Alembic, ver README):** `0002` faz `ALTER COLUMN ... TYPE NUMERIC(14,3) USING ROUND(col::numeric, 3)` sem apagar dados, e é idempotente.
  Testada em banco descartável com o esquema antigo: tipos convertidos, dados preservados (`0.30000000000000004` virou `0.300`). O `ALTER` reescreve
  a tabela e trava as linhas, então rode com a API parada. Se a API subir num banco ainda em `FLOAT`, o `init_models()` registra um aviso.
* **Testes** (`tests/test_decimal.py`): 0,1 somado 10 mil vezes dá **exatamente** 1000 (no modelo e em `SUM` no banco; com `float` dá 1000,0000000001588);
  conservação **exata** (`==`, sem tolerância) com 0,1/0,25/12,345; cenário 100/40/30/50 igual; estresse com 200 requisições fracionadas e 8 workers; JSON
  numérico; recusa de excesso de casas. Um teste do seed teve só a conta ajustada para `Decimal`.
* **Desempenho** (benchmark `--rapido`, 300 requisições): a comparação **simples** antes/depois piorou (ex.: 16 workers, alta contenção, speedup 3,34 → 1,74), mas a
  máquina estava muito ruidosa (a linha sequencial também oscilou de 5,2 s a 7,6 s). Por isso fiz um **A/B intercalado** (código antigo e novo alternados,
  3 pares, baixa contenção): vazão com 4 workers antiga 114/115/101 req/s contra nova 81/95/113; razão 4 workers / sequencial 2,8/2,8/2,7 contra 2,7/2,4/2,6. No pior par a razão caiu 14 % (2,75 → 2,36) e nos outros dois 4 %; no par 1 a vazão absoluta nova foi 29 % menor, mas a linha sequencial do mesmo par também caiu 26 %. Sem tendência consistente, **não há evidência clara de custo do `Decimal`**, mas com 3 pares e esse ruído não dá para afirmar que o custo é zero.

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

**Correção aplicada (Sprint 05): "senhas por material".** `FilaRequisicoes.adicionar` recebe o `material_id`; ao retirar um item, **dentro do
mesmo lock da fila**, ele ganha uma `Senha` na fila do seu material (ordem da senha = ordem de retirada = prioridade). O worker só abre a
transação quando o item anterior do mesmo material já **travou o lock do material** (gancho `ao_travar_material` de `processar_requisicao`);
como o próximo só pede o lock depois do anterior, a ordem é estrita. Materiais diferentes seguem em paralelo, e a senha é liberada sempre (`finally`),
mesmo se o processamento falhar. Itens sem `material_id` continuam funcionando (não bloqueiam ninguém).

*Versão 1 (descartada).* A primeira versão liberava a senha só no fim do item anterior. Funcionava (0 inversões), mas **serializava a transação inteira**:
com 4 workers no mesmo material o speedup sobre a linha de base caiu de ≈ 1,96× para ≈ 0,95× (A/B intercalado, `docs/benchmark.md` §11).
A versão 2 (liberar ao travar o lock) manteve a ordem e recuperou o desempenho (≈ 1,93×).

**Medição antes/depois** (3 obras disputam 10 unidades, entram na fila em ordem inversa à prioridade; latência de abertura de
conexão aleatória de 0 a 15 ms, semente fixa; 150 tentativas por linha):

| Workers | Antes: obra mais prioritária levou o estoque | Depois |
|---|--:|--:|
| 1 (controle) | 60/60 (0 % de inversões) | n/a |
| 3 | 50/150 (**66,7 %** de inversões) | **150/150 (0 %)** |
| 8 | 47/150 (**68,7 %** de inversões) | **150/150 (0 %)** |

(As taxas "antes" são maiores que as da tabela do início do achado porque a latência aleatória de 0 a 15 ms é uma condição severa e deixa a corrida quase aleatória.)
O `xfail` de `tests/test_prioridade_concorrente.py` virou teste normal; há um novo com 25 rodadas de latência aleatória e 8 testes novos em
`tests/test_senhas_fila.py`.

## 15. Mensagens de validação em inglês e eco do valor enviado (MÉDIA)
**O que acontecia.** Respostas 422 vinham do Pydantic, em inglês e com o nome técnico do campo (`quantidade_estoque: Input should be greater than or equal to 0`),
e traziam o valor enviado em `input` (uma senha curta digitada voltava em texto claro na resposta). Textos padrão do FastAPI (`Not authenticated`) também em inglês.

**Correção aplicada (Sprint 05).** `app/core/erros.py` registra tratadores que devolvem `{"detail": [{"loc", "campo", "msg", "type"}]}`, com o rótulo do campo
(«Quantidade em estoque») e a mensagem em português («deve ser maior ou igual a 0»), sem `input` nem `ctx`; traduz também 401/404/405. O frontend usa o
`campo` quando existe. Os `detail` já escritos em português pelas rotas não mudam. 34 testes em `tests/test_mensagens_erro.py`.
