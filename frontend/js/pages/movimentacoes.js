import {api, apiJson, atrasar, comBotao, esc, fmt, formJson, dataHora, paginador, qs, toast, $} from '../api.js';

export const titulo = 'Movimentações';
export const perfis = ['gestor', 'almoxarife'];

const TIPOS = {entrada: '⬆️ Entrada', saida: '⬇️ Saída', ajuste: '✏️ Ajuste'};

export async function montar(el, usuario, params) {
  let materiais = [], pagina = 1;
  const tamanho = 15;

  el.innerHTML = `<h1 class="pagina-titulo">Movimentações de estoque</h1>
    <div class="grid2">
      <section id="secEntrada"><h2>Registrar entrada (recebimento/compra)</h2>
        <form id="fEntrada">
          <label class="full">Material<select name="material_id" class="selMat" required></select></label>
          <label>Quantidade<input name="quantidade" type="number" min="0.001" step="any" required></label>
          <label>Observação (opcional)<input name="observacao" maxlength="500" placeholder="Ex.: NF 1234"></label>
          <button class="full" type="submit">Registrar entrada</button>
        </form></section>
      <section><h2>Registrar ajuste de inventário</h2>
        <form id="fAjuste">
          <label class="full">Material<select name="material_id" class="selMat" required></select></label>
          <label>Quantidade (+ soma, − subtrai)<input name="quantidade" type="number" step="any" required placeholder="-5 ou 12"></label>
          <label>Motivo (obrigatório)<input name="observacao" required minlength="3" maxlength="500" placeholder="Ex.: contagem do inventário"></label>
          <button class="full" type="submit">Registrar ajuste</button>
        </form></section>
    </div>
    <section><h2>Histórico</h2>
      <div class="filtros">
        <label>Material<select id="fMaterial"><option value="">Todos</option></select></label>
        <label>Tipo<select id="fTipo"><option value="">Todos</option>${Object.entries(TIPOS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
        <label>De<input id="fDe" type="date"></label><label>Até<input id="fAte" type="date"></label>
        <label>Busca<input id="fBusca" type="search" placeholder="material ou observação"></label>
      </div>
      <div class="tw"><table id="tab"></table></div><div id="pag"></div></section>`;

  async function carregar() {
    const r = await api('/movimentacoes' + qs({
      material_id: $('#fMaterial', el).value, tipo: $('#fTipo', el).value, de: $('#fDe', el).value,
      ate: $('#fAte', el).value, busca: $('#fBusca', el).value.trim(), pagina, tamanho}));
    $('#tab', el).innerHTML = r.itens.length
      ? `<tr><th>Data</th><th>Material</th><th>Tipo</th><th>Quantidade</th><th>Saldo após</th><th>Responsável</th><th>Observação / origem</th></tr>` +
        r.itens.map(m => `<tr><td>${dataHora(m.criado_em)}</td><td>${esc(m.material_nome)}</td><td>${TIPOS[m.tipo]}</td>
          <td>${m.tipo === 'saida' ? '−' : (m.quantidade > 0 ? '+' : '')}${fmt(m.quantidade)} ${esc(m.unidade)}</td>
          <td>${fmt(m.saldo_apos)} ${esc(m.unidade)}</td><td>${esc(m.usuario_nome ?? 'sistema')}</td>
          <td class="desc">${esc(m.observacao ?? (m.requisicao_id ? 'Requisição #' + m.requisicao_id.slice(0, 6) : '—'))}</td></tr>`).join('')
      : '<tr><td class="empty">Nenhuma movimentação encontrada.</td></tr>';
    paginador($('#pag', el), {pagina: r.pagina, tamanho: r.tamanho, total: r.total}, p => { pagina = p; carregar(); });
  }
  const recarregarDoZero = () => { pagina = 1; carregar().catch(e => toast(e.message, 'err')); };

  for (const id of ['#fMaterial', '#fTipo', '#fDe', '#fAte']) $(id, el).addEventListener('change', recarregarDoZero);
  $('#fBusca', el).addEventListener('input', atrasar(recarregarDoZero));

  for (const [idForm, caminho, ok] of [['#fEntrada', '/movimentacoes/entrada', 'Entrada registrada'], ['#fAjuste', '/movimentacoes/ajuste', 'Ajuste registrado']]) {
    $(idForm, el).addEventListener('submit', async e => {
      e.preventDefault();
      await comBotao($('[type=submit]', e.target), async () => {
        try {
          const m = await apiJson(caminho, 'POST', formJson(e.target));
          toast(`${ok}. Saldo de ${m.nome}: ${fmt(m.quantidade_estoque)} ${m.unidade}`, 'ok');
          e.target.reset(); selecionarMaterial(m.id);
          materiais = await api('/materiais'); recarregarDoZero();
        } catch (err) { toast(err.message, 'err'); }
      });
    });
  }

  const selecionarMaterial = id => el.querySelectorAll('.selMat').forEach(s => { if (id) s.value = id; });
  materiais = await api('/materiais');
  const opcoes = materiais.map(m => `<option value="${m.id}">${esc(m.nome)} (${fmt(m.quantidade_estoque)} ${esc(m.unidade)})</option>`).join('');
  el.querySelectorAll('.selMat').forEach(s => s.innerHTML = opcoes);
  $('#fMaterial', el).insertAdjacentHTML('beforeend', materiais.map(m => `<option value="${m.id}">${esc(m.nome)}</option>`).join(''));

  // vindo do alerta do dashboard: #/movimentacoes?entrada=ID
  const pre = params.get('entrada');
  if (pre) { selecionarMaterial(pre); $('#secEntrada', el).scrollIntoView(); $('#fEntrada [name=quantidade]', el).focus(); }
  await carregar();
}
