# Relatórios, dashboard, movimentações e diagnóstico de déficit

Este documento explica **como cada número é calculado**, a **regra do semáforo 🟢🟡🔴**, a **regra de priorização**
usada na simulação e as **limitações** do diagnóstico.

## 1. Diagnóstico de déficit

`GET /diagnostico/deficit[?material_id=]` · `POST /diagnostico/simular` · tela **Diagnóstico de déficit**
(gestor e almoxarife; é uma visão global do estoque, então o engenheiro recebe 403).

### O que ele faz
Responde: *"se os pedidos abertos fossem atendidos agora, quem receberia quanto, e o que faltaria?"*
É uma **simulação somente leitura** — nada é gravado, nenhum lock é tomado e nenhum worker é acionado.

1. **Demanda aberta** = requisições `PENDENTE` e `EM_PROCESSAMENTO` (somadas por material com `GROUP BY` no SQL).
2. Para cada material, as requisições abertas são **ordenadas pela mesma regra da fila**
   (`app/services/prioridade.py`, usada tanto por `FilaRequisicoes` quanto pelo diagnóstico):

   | # | Critério | Vem primeiro |
   |---|---|---|
   | 1 | Obra **parada por falta de material** | parada |
   | 2 | **Nível de prioridade** da obra | menor número (1 = mais urgente) |
   | 3 | **Prazo de entrega** | prazo mais próximo (sem prazo = por último) |
   | 4 | **Data de criação** do pedido | mais antiga (FIFO como desempate) |

3. O estoque atual é **distribuído nessa ordem**, com a mesma regra do `estoque_service`: atende o que houver;
   se o saldo acabar no meio, o pedido é parcial; zerou, o resto fica sem atendimento.
4. A diferença `demanda − estoque` (se positiva) é o **déficit**.

Um teste garante que, para as mesmas requisições, **a ordem do diagnóstico é idêntica à ordem em que a fila as entregaria**
(`tests/test_diagnostico.py::test_ordem_do_diagnostico_identica_a_ordem_da_fila`), e outro garante que extrair a chave de
prioridade para um módulo compartilhado foi uma refatoração pura (`tests/test_prioridade_refator.py`).

### Exemplo (cenário do enunciado)
100 m de cabo 10 mm². Pedidos: Obra C (parada) 50 m, Obra A (prioridade 2) 40 m, Obra B (prioridade 3) 30 m.

```
🔴 Cabo 10 mm² insuficiente. Estoque: 100 m. Demanda: 120 m. Déficit: 20 m

 #  Obra    Pediu   Receberia   Ficaria pendente   Situação
 1  Obra C  50 m    50 m        0 m                🟢 Atendida por completo
 2  Obra A  40 m    40 m        0 m                🟢 Atendida por completo
 3  Obra B  30 m    10 m        20 m               🟡 Atendida parcialmente
```

### Semáforo por material
`restante = estoque − min(demanda, estoque)` (o que sobraria depois de atender a demanda).

| Ícone | Condição | Mensagem |
|---|---|---|
| 🟢 verde | `demanda ≤ estoque` **e** `restante > crítico` | estoque cobre a demanda e o restante fica acima do crítico |
| 🟡 amarelo | `demanda ≤ estoque` **e** `restante ≤ crítico` | cobre a demanda, mas o que sobra fica no crítico ou abaixo |
| 🔴 vermelho | `demanda > estoque` (déficit > 0) | `<Material> insuficiente. Estoque: … Demanda: … Déficit: …` |

* O limite usa `≤` de propósito: é a mesma regra do **alerta de estoque crítico** do dashboard e dos relatórios
  (material com estoque **igual** ao crítico já está em alerta).
* Material **sem demanda** é 🟢 se o estoque está acima do crítico e 🟡 se está no crítico ou abaixo
  ("sem demanda aberta, mas o estoque está no nível crítico").

### Semáforo por requisição
🟢 recebe tudo (`atendida = pedida`) · 🟡 recebe parte · 🔴 não recebe nada. Na tela, **cada ícone vem com texto**
("Atendida por completo", "Atendida parcialmente", "Sem atendimento") e o card traz o nome do estado
("Estoque suficiente", "Atenção", "Déficit"): a informação não depende só da cor (acessibilidade).

### Déficit histórico
À parte da simulação, mostra o que **já aconteceu**: para pedidos `PARCIAL` e `NEGADA`,
`Σ (solicitada − atendida)` por material, com a contagem de parciais e negadas.

### "E se…?" — `POST /diagnostico/simular`
Recebe requisições **hipotéticas** (`{obra_id, material_id, quantidade}`) e as junta à demanda aberta, como se tivessem
acabado de chegar (ficam **atrás** dos pedidos de mesma prioridade, mas **passam à frente** dos de prioridade menor).
Resposta idêntica à do diagnóstico, com `simulacao_hipotetica: true` e as linhas marcadas `hipotetica: true`. Nada é gravado.

### Consistência da leitura
O diagnóstico faz várias consultas (materiais, demanda, linhas, histórico). Para que enxerguem **o mesmo instante** do banco,
ele roda em `REPEATABLE READ`: um pedido processado no meio da leitura não deixa o resultado incoerente.

### Limitações (leia antes de apresentar o diagnóstico como "previsão")
* **É uma simulação do estado atual, não uma garantia.** Novos pedidos, entradas de estoque, ajustes ou mudanças de prioridade
  alteram o resultado real. É um retrato do momento em que foi gerado (`gerado_em`).
* **Ordem estrita × concorrência (medido).** A fila *retira* os itens em ordem de prioridade, mas com N workers eles são processados
  ao mesmo tempo, e o primeiro a obter o lock do material nem sempre é o mais prioritário. O diagnóstico assume a ordem estrita.
  Em experimentos controlados (obras disputando um estoque que só atende uma), a obra mais prioritária **sempre** levou o estoque com
  1 worker, mas **perdeu para outra em 0 % a 35 % das tentativas com 3 ou 8 workers**, conforme as condições (conexões frias ou quentes,
  outro tráfego): é uma corrida, não um número fixo.
  Na demonstração com 3 workers (logo após reiniciar a API), 6 dos 8 pedidos abertos saíram como previsto; com **1 worker, 8 de 8**.
  Isso é um **achado do motor de filas** (nº 14 em `docs/achados.md`), não do diagnóstico: a previsão descreve a ordem *correta*.
* **Chave da fila congelada.** A fila calcula a chave de prioridade **quando o item entra**; se a obra mudar de prioridade ou de
  "parada" depois, o item já enfileirado mantém a chave antiga, enquanto o diagnóstico usa os dados **atuais** da obra.
* `EM_PROCESSAMENTO` conta como demanda aberta: o débito dele só aparece no estoque após o commit da transação.
* Quantidades em ponto flutuante (`Float`): somas muito longas podem acumular erro de arredondamento.
* Considera **cada material isoladamente** (um pedido com vários itens não é tratado como unidade).

## 2. Movimentações de estoque (livro-razão)

Tabela `movimentacoes_estoque` · tela **Movimentações** · `GET /movimentacoes`, `POST /movimentacoes/entrada`, `POST /movimentacoes/ajuste`
(gestor e almoxarife).

| Tipo | Quem registra | `quantidade` | Observação |
|---|---|---|---|
| `ENTRADA` | almoxarife/gestor (recebimento, compra); também o saldo inicial ao cadastrar um material | positiva | opcional |
| `SAIDA` | **o processamento da requisição**, na mesma transação do débito | positiva | origem = requisição |
| `AJUSTE` | almoxarife/gestor (inventário) | **com sinal** (+ soma, − subtrai) | **motivo obrigatório** |

**Invariante:** `saldo inicial + entradas + ajustes − saídas == estoque atual`, e cada linha guarda o `saldo_apos`.
Entradas e ajustes usam `SELECT … FOR UPDATE` na linha do material, como os workers; um ajuste que deixaria o estoque
negativo é recusado (409) sem registrar nada. A SAÍDA entra **depois** da decisão e do débito, **sem alterar** a regra
de alocação, o lock nem a ordem das operações. Como é gravada sob o lock do material, a ordem dos `id` das movimentações de
um material é a ordem em que o lock foi obtido (por isso o teste de estresse consegue conferir a cadeia de saldos).

Editar um material pela tela **não altera o saldo**; para mudá-lo use Entrada ou Ajuste (que registram quem, quando e por quê).
(A API ainda aceita `quantidade_estoque` em `PUT /materiais/{id}` por compatibilidade: a diferença vira um `AJUSTE` com motivo genérico.)

## 3. Relatórios

Todos aceitam `de`/`ate` (datas inclusivas), `obra_id`, `material_id`, `status` quando fazem sentido, `ordenar`/`direcao`,
paginação (`pagina`, `tamanho`) e `formato=csv`. Os totais são calculados no banco (`GROUP BY`, `SUM`, `COUNT`, `percentile_cont`).
**O período filtra pela data de criação do pedido** e as datas são **UTC** (como o resto do sistema).

| Relatório | Endpoint | O que mostra | Quem |
|---|---|---|---|
| Consumo | `/relatorios/consumo?agrupar=obra_material\|material\|obra` | quantidade atendida × solicitada e nº de requisições (padrão: pedidos aprovados/parciais). Por **obra** conta *itens atendidos*, porque unidades diferentes não se somam | todos* |
| Requisições por período | `/relatorios/requisicoes-por-periodo?agrupar=dia\|semana` | série temporal por status (semana começa na segunda) | todos* |
| Estoque crítico | `/relatorios/estoque-critico` | materiais com estoque ≤ crítico, **quanto falta para o crítico**, % do crítico e situação (Zerado / Abaixo / No limite) | todos |
| Taxa de atendimento | `/relatorios/taxa-atendimento` | % aprovados/parciais/negados (só pedidos já resolvidos), % atendido em quantidade e **déficit acumulado por material** | todos* |
| Desempenho | `/relatorios/desempenho` | espera na fila (`processado_em − criado_em`): média, p50, p95, máx., e processadas **por worker** | gestor, almoxarife |

\* O **engenheiro** só vê a própria obra (pedir outra devolve 403); o relatório de desempenho é restrito a gestor e almoxarife.

### CSV compatível com Excel (pt-BR)
Separador `;`, **UTF-8 com BOM**, cabeçalho em português, números com **vírgula decimal**, datas `dd/mm/aaaa`.
Textos que começam com `=`, `+`, `-` ou `@` recebem um `'` na frente, para o Excel não executá-los como fórmula
(nomes de material vêm de usuários). A exportação ignora a paginação (até 50 000 linhas).

## 4. Dashboard

`GET /dashboard/completo?periodo=7|30|todos` (uma chamada alimenta a tela inteira; atualiza a cada 10 s).

| Card | Definição |
|---|---|
| Total de materiais | quantidade de materiais cadastrados |
| Materiais em alerta | estoque ≤ estoque crítico |
| Requisições abertas | pendentes + em processamento (agora, independentemente do período) |
| Taxa de atendimento | pedidos **aprovados** ÷ pedidos já resolvidos (aprovados + parciais + negados) no período |
| Materiais em déficit | materiais cuja **demanda aberta** é maior que o estoque (🔴 do diagnóstico). Só gestor/almoxarife |

Gráficos (Chart.js 4.4.1 por cdnjs, sem build): requisições por dia (linha; dias sem pedido aparecem com 0), distribuição por
status (rosca), **top 5 materiais mais consumidos** (barras) e **itens atendidos por obra** (barras). O top 5 ordena por
quantidade atendida; como as unidades diferem (m, un, cx…), o rótulo mostra a unidade e a comparação entre materiais de unidades
diferentes deve ser lida com isso em mente. O **engenheiro** vê tudo restrito à própria obra.
O painel de **alertas** lista os materiais críticos com um atalho para registrar entrada.

## 5. Auditoria (somente gestor)

Tabela `auditoria`, tela **Auditoria**, `GET /auditoria` com filtros (usuário, ação, entidade, período) e paginação.
Ações registradas: `login`, `login_falhou`, `login_negado_inativo`, `usuario_criado`, `usuario_editado`, `senha_redefinida`,
`material_criado`, `material_editado`, `material_removido`, `obra_criada`, `obra_andamento`, `obra_removida`, `entrada_estoque`,
`ajuste_estoque`, `requisicao_criada`, `pedido_criado`. O registro entra **na mesma transação** da ação.
**Senhas e tokens nunca são gravados**: o login registra só o e-mail informado, e o serviço descarta qualquer chave
sensível (`senha`, `nova_senha`, `token`…) como segunda barreira (há testes que varrem a auditoria procurando as senhas usadas).

## 6. Índices
`requisicoes`: `status`, `criado_em`, `obra_id`, `material_id` (e `pedido_id`, que já existia). `movimentacoes_estoque`:
`(material_id, criado_em)`, `criado_em`, `requisicao_id`. `auditoria`: `acao`, `usuario_id`, `criado_em`.
Em bancos antigos, `init_models()` cria os índices de `requisicoes` com `CREATE INDEX IF NOT EXISTS`.
O custo dos índices e do registro da movimentação no processamento foi medido em `docs/benchmark.md`.

## 7. Dados de demonstração
`python -m scripts.seed_demo` (idempotente) / `--reset` (apaga o banco apontado e pede confirmação). Todos os dados são **fictícios**
e identificados como demonstração (e-mails `@demo.local`, observações "(demonstração)", ação de auditoria `seed_demo`).
Para o diagnóstico mostrar os pedidos abertos sem que os workers os processem na hora, suba a API com `N_WORKERS=0`;
depois reinicie com `N_WORKERS=1` e compare o que o diagnóstico previu com o que realmente aconteceu (8 de 8 iguais; com 3 workers a
ordem pode trocar quando dois pedidos do mesmo material correm juntos — ver limitações)).
