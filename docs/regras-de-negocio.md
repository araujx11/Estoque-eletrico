# Regras de negócio

Situação das regras definidas na Sprint 01 (RN01 a RN13) e as regras **novas ou atualizadas na Sprint 05**, com a justificativa
e o lugar onde cada uma é garantida (código e testes).

## 1. Regras da Sprint 01

| Código | Regra | Situação | Onde é garantida |
|---|---|---|---|
| RN01 | Um material não poderá possuir quantidade negativa | Implementada | Lock no material em `estoque_service`; `quantidade_estoque >= 0` na validação; ajuste que deixaria o saldo negativo é recusado (409). Testes: `test_regras_estoque`, `test_concorrencia`, `test_movimentacoes` |
| RN02 | Somente usuários autorizados poderão alterar o estoque | Implementada | Perfis gestor e almoxarife (`app/core/auth.py`). Teste: `test_permissoes` |
| RN03 | Toda saída deve estar associada a uma solicitação ou operação autorizada | Implementada | A saída só nasce em `processar_requisicao` e guarda o `requisicao_id` |
| RN04 | Toda movimentação deve possuir data, hora e responsável | **Parcial** | Data e hora sempre; responsável direto em entradas e ajustes (`usuario_id`); nas saídas, pela requisição (`solicitante_id`) |
| RN05 | Uma solicitação não poderá retirar quantidade superior à disponível | Implementada | Resultado aprovada / parcial / negada conforme o saldo lido sob lock |
| RN06 | Uma solicitação aprovada atualiza o estoque de forma transacional | Implementada | Débito, saída no livro-razão e status na mesma transação |
| RN07 | Operações simultâneas respeitam mecanismos de sincronização | Implementada e **atualizada (RN15)** | `SELECT ... FOR UPDATE`; a ordem de atendimento passou a ser garantida (Sprint 05) |
| RN08 | Cada obra possui identificação única | Implementada | Chave primária |
| RN09 | Cada material possui identificação única | Implementada | Chave primária e nome único |
| RN10 | Uma devolução atualiza a quantidade disponível | **Implementada (RN21)** | Tipo de movimentação `devolucao` e `POST /movimentacoes/devolucao` |
| RN11 | Materiais abaixo do estoque mínimo são identificados | Implementada | `estoque_critico`; alertas no dashboard e relatório |
| RN12 | Operações críticas são registradas no histórico | Implementada | `movimentacoes_estoque` e `auditoria` |
| RN13 | Não aprovar solicitações que excedam o estoque após as regras de concorrência | Implementada | Idem RN05, sob lock; teste de estresse com 200 requisições |

## 2. Regras novas ou atualizadas na Sprint 05

### RN14 (nova) — O processamento de uma requisição é idempotente
**Regra.** Uma requisição só pode ser debitada uma vez. Processar de novo uma requisição já **aprovada, parcial ou negada** não altera o estoque,
o status, o worker, a data de processamento nem o livro-razão. Uma requisição em **erro** pode ser processada de novo, porque a transação que falhou foi desfeita.
**Justificativa.** Achado 1: com a fila em memória, uma requisição pode entrar duas vezes (reinício da API, vários processos), e o processamento
a debitava duas vezes (estoque 40 em vez de 70).
**Implementação.** `processar_requisicao` trava a requisição (`FOR UPDATE`) antes do material (ordem única: requisição → material) e ignora as já resolvidas.
**Testes.** `tests/test_reprocessamento.py` (sequencial, parcial/negada, 10 workers na mesma requisição, requisição em erro, gancho da fila).

### RN15 (nova, atualiza a RN07 e o RF22) — A prioridade vale na alocação do estoque
**Regra.** Entre pedidos do **mesmo material**, é atendido primeiro o de maior prioridade (obra parada por falta de material, depois nível de prioridade,
depois prazo mais próximo, depois o mais antigo), **mesmo com vários workers**. Pedidos de materiais diferentes continuam sendo processados em paralelo.
**Justificativa.** Achado 14: a fila retirava os pedidos na ordem certa, mas, com vários workers, quem chegava primeiro ao lock do material levava o estoque
(a obra mais prioritária perdia em 66,7 % das tentativas com 3 workers, num experimento com latência de conexão aleatória). Priorizar é o requisito central da fila.
**Implementação.** "Senhas por material" (`fila_service.Senha`): ao retirar um item, a fila lhe dá uma senha na fila do seu material; o worker só abre a transação depois
que o item anterior do mesmo material **travou o lock do material**. A senha é sempre liberada, mesmo se o processamento falhar.
**Testes.** `tests/test_prioridade_concorrente.py` (determinístico e com latências aleatórias), `tests/test_senhas_fila.py`. **Medição.** 0 inversões em 150 tentativas com 3 e com 8 workers (antes: 66,7 % e 68,7 %).
Custo de desempenho: sem perda além do ruído do ambiente (`docs/benchmark.md` §11).

### RN16 (nova) — Mensagens de erro em português, sem devolver o valor enviado
**Regra.** Toda mensagem de erro mostrada ao usuário está em português e diz qual campo tem problema («Quantidade em estoque: deve ser maior ou igual a 0»).
A resposta de erro nunca devolve o valor que o usuário enviou (por exemplo, uma senha digitada).
**Justificativa.** Achado 15: as respostas de validação vinham em inglês, com o nome técnico do campo, e ecoavam o valor enviado.
**Implementação.** `app/core/erros.py` (tratadores de validação e de erros HTTP padrão) e uso do rótulo do campo no frontend (`frontend/js/api.js`).
**Testes.** `tests/test_mensagens_erro.py` (34 testes).

## 2-B. Regras novas (achados 2, 3 e 8)

### RN17 (nova) — Quantidades são exatas (3 casas decimais)
**Regra.** Toda quantidade (estoque, estoque crítico, solicitada, atendida, movimentação, saldo) é um decimal exato com **até 3 casas** e até 11 dígitos
inteiros. Somas e subtrações não acumulam erro de arredondamento: "saldo inicial + entradas + ajustes − saídas == estoque atual" vale com igualdade exata.
Valores com mais casas ou dígitos são recusados (422), nunca arredondados em silêncio.
**Justificativa.** Achado 8: com `float`, 0,1 somado 10 mil vezes dava 1000,0000000001588.
**Implementação.** `NUMERIC(14,3)` no banco, `decimal.Decimal` no código, migração Alembic `0002`. O JSON continua numérico.
**Testes.** `tests/test_decimal.py`.

### RN18 (nova, atualiza a RN14) — Cada requisição entra em uma única fila
**Regra.** Com vários processos da API, cada requisição aberta é **reivindicada** por um único processo antes de ir para a fila em memória dele. A reivindicação vale por
um prazo (`FILA_LEASE_S`); vencido, outro processo pode retomá-la.
**Justificativa.** Achado 2: todo processo reenfileirava todas as pendentes. A correção do estoque continua sendo da RN14 (idempotência): a reivindicação evita
trabalho duplicado, não é ela que protege o saldo.
**Implementação.** `fila_service.reivindicar_e_enfileirar` (`UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING`). **Limites** (achado 17): a
prioridade entre processos não é estrita; o prazo não é renovado.
**Testes.** `tests/test_fila_erros.py` (Parte A).

### RN19 (nova, atualiza a RN14) — Requisições com erro são reprocessadas, com limite
**Regra.** Uma requisição que falha ao ser processada fica em `ERRO` com o contador `tentativas` e o `ultimo_erro`. Uma rotina periódica a recoloca na fila, com espera
crescente entre as tentativas, até `ERRO_MAX_TENTATIVAS` (padrão 3). Depois disso é **ERRO definitivo**: não é mais reprocessada, é registrada na auditoria
(`requisicao_erro_definitivo`) e aparece no diagnóstico. Uma requisição já aprovada, parcial ou negada **nunca** vira `ERRO` (nem é reprocessada).
**Justificativa.** Achado 3 (falha transitória virava erro definitivo) e achado 16 (`ERRO` sobre requisição já debitada levaria a débito em dobro).
**Implementação.** `fila_service._marcar_erro`, `reprocessar_erros` e `reprocessar_erros_loop`; configuração por variáveis de ambiente (README).
**Testes.** `tests/test_fila_erros.py` (Parte B).

### RN20 (nova, RF16) — Toda movimentação registra o responsável
**Regra.** Entradas, ajustes, devoluções e **saídas** guardam quem foi o responsável. Na saída gerada pelo processamento de uma requisição, o responsável é o **solicitante** da
requisição; se ela não tem solicitante (registro anterior à autenticação), fica vazio, sem erro (a tela mostra "sistema"). A migração `0004` preenche as saídas já existentes.
**Testes.** `tests/test_responsavel_saidas.py`.

### RN21 (RN10 e RF15 implementadas) — Devolução de material
**Regra.** Gestor e almoxarife (o engenheiro não) registram a devolução de material: o saldo **aumenta**, e a movimentação guarda o saldo depois, o responsável, o motivo (**obrigatório**, mín. 3 caracteres) e,
opcionalmente, a obra e a requisição de origem. **Não se devolve mais do que foi atendido** para a obra e o material (soma de `quantidade_atendida` das requisições aprovadas ou parciais, menos as devoluções já feitas);
passando disso, 409 com mensagem em português. Sem obra informada, o limite é o total atendido do material. A requisição informada precisa ser do mesmo material (e da obra, se informada); a obra de origem vem dela.
O relatório de consumo mostra a quantidade atendida **menos as devoluções** (por obra e material, no período).
**Implementação.** `POST /movimentacoes/devolucao`: trava o material (`FOR UPDATE`, o mesmo lock dos workers), confere o limite, soma ao estoque, grava a movimentação e a auditoria (`devolucao_estoque`)
na mesma transação. Como o limite é conferido sob o lock do material, duas devoluções simultâneas não furam o limite. O livro-razão passa a ser: saldo inicial + entradas + ajustes + devoluções − saídas = estoque atual.
**Limitações.** O limite é conferido por obra e material (não por requisição); devoluções sem obra só entram na conta do limite geral do material. O "Top materiais" do dashboard ainda não desconta devoluções.
**Testes.** `tests/test_devolucoes.py` (15: válida, acima do limite, parcial, por obra, inexistentes, validação, permissões, relatório e concorrência com pedidos).

### RN22 (nova, achado 11) — Materiais e obras são desativados, não apagados
**Regra.** Remover um material ou uma obra o **desativa**; o histórico (requisições, movimentações, devoluções, auditoria) permanece. Desativados somem das listagens (o gestor os vê com `incluir_inativos`), não aceitam
novos pedidos (nem entradas, no caso do material) e não entram na fila. Podem ser **reativados** pelo gestor, sem recriar. Não se desativa com requisições pendentes ou em processamento.
**Implementação e testes.** Colunas `ativo`/`desativado_em` (migração `0006`); `tests/test_exclusao_logica.py` (13 testes). Detalhes em `docs/achados.md` (achado 11).

## 3. Regras que já existiam no código e não estavam no planejamento

Registradas agora, para o documento refletir o que o sistema de fato faz:

| Regra | Onde |
|---|---|
| O engenheiro só cria pedidos e só vê dados da **própria obra** (403 nas demais) | `app/api/routes.py`, `app/core/auth.py` |
| **Obra finalizada** não aceita novos pedidos (409) | `criar_pedido` em `app/api/routes.py` |
| O **ajuste de inventário** exige motivo (mínimo de 3 caracteres), não pode ser zero e não pode deixar o estoque negativo | `app/schemas.py`, `app/api/estoque_routes.py` |
| Um pedido **não pode repetir o mesmo material** (422) | `app/schemas.py`; teste em `test_api_validacao` |
| Livro-razão: saldo inicial + entradas + ajustes − saídas = estoque atual | `app/models.py` (`MovimentacaoEstoque`) |
| Usuário **desativado** não consegue entrar (403) | `app/api/auth_routes.py` |
