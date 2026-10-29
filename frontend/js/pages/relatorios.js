import {api, atrasar, baixarCsv, celula, esc, fmt, paginador, qs, toast, $, STATUS} from '../api.js';

export const titulo = 'Relatórios';
export const perfis = ['gestor', 'almoxarife', 'engenheiro'];

// cada relatório: endpoint, filtros que aceita e opções de agrupamento/ordenação
const RELATORIOS = {
  consumo: {nome: 'Consumo', rota: '/relatorios/consumo', filtros: ['periodo', 'obra', 'material', 'agrupar'], ordenar: 'quantidade',
    agrupar: [['obra_material', 'Obra e material'], ['material', 'Por material'], ['obra', 'Por obra']]},
  periodo: {nome: 'Requisições por período', rota: '/relatorios/requisicoes-por-periodo', filtros: ['periodo', 'obra', 'material', 'agrupar'], ordenar: null,
    agrupar: [['dia', 'Por dia'], ['semana', 'Por semana']]},
  critico: {nome: 'Estoque crítico', rota: '/relatorios/estoque-critico', filtros: ['busca'], ordenar: 'percentual'},
  taxa: {nome: 'Taxa de atendimento', rota: '/relatorios/taxa-atendimento', filtros: ['periodo', 'obra', 'material'], ordenar: 'deficit'},
  desempenho: {nome: 'Desempenho do processamento', rota: '/relatorios/desempenho', filtros: ['periodo'], ordenar: 'processadas', soGestorAlmox: true},
};
const NOMES_RESUMO = {
  total_resolvidas: 'Pedidos resolvidos', aprovadas: 'Aprovadas', parciais: 'Parciais', negadas: 'Negadas',
  pct_aprovadas: '% aprovadas', pct_parciais: '% parciais', pct_negadas: '% negadas', taxa_atendimento_quantidade_pct: '% atendido (quantidade)',
  processadas: 'Processadas', espera_media_s: 'Espera média (s)', espera_p50_s: 'Espera p50 (s)', espera_p95_s: 'Espera p95 (s)', espera_max_s: 'Espera máx. (s)',
};

export async function montar(el, usuario) {
  const eng = usuario.perfil === 'engenheiro';
  const abas = Object.entries(RELATORIOS).filter(([, r]) => !(r.soGestorAlmox && eng));
  let atual = abas[0][0], pagina = 1, ultimaConsulta = null;
  let obras = [], materiais = [];

  el.innerHTML = `<h1 class="pagina-titulo">Relatórios</h1>
    <section><div class="tabs" id="abas">${abas.map(([k, r]) => `<button type="button" data-k="${k}">${r.nome}</button>`).join('')}</div>
      <div class="filtros" id="filtros"></div>
      <div class="linha" style="margin-bottom:10px"><button id="csv" type="button" class="sm">⬇ Exportar CSV</button>
        <span class="mut">O período considera a data de criação do pedido.</span></div>
      <div id="resumo" class="resumo-box"></div>
      <div class="tw"><table id="tab"></table></div><div id="pag"></div></section>`;

  const opcoes = (lista, rotulo) => `<option value="">${rotulo}</option>` + lista.map(x => `<option value="${x.id}">${esc(x.nome)}</option>`).join('');

  function desenharFiltros() {
    const r = RELATORIOS[atual];
    const partes = [];
    if (r.filtros.includes('periodo')) partes.push(`<label>De<input data-f="de" type="date"></label><label>Até<input data-f="ate" type="date"></label>`);
    if (r.filtros.includes('obra') && !eng) partes.push(`<label>Obra<select data-f="obra_id">${opcoes(obras, 'Todas')}</select></label>`);
    if (r.filtros.includes('material')) partes.push(`<label>Material<select data-f="material_id">${opcoes(materiais, 'Todos')}</select></label>`);
    if (r.agrupar) partes.push(`<label>Agrupar<select data-f="agrupar">${r.agrupar.map(([v, n]) => `<option value="${v}">${n}</option>`).join('')}</select></label>`);
    if (r.filtros.includes('busca')) partes.push(`<label>Buscar material<input data-f="busca" type="search"></label>`);
    el.querySelector('#filtros').innerHTML = partes.join('');
    el.querySelectorAll('#filtros [data-f]').forEach(c => c.addEventListener(c.type === 'search' ? 'input' : 'change',
      c.type === 'search' ? atrasar(() => { pagina = 1; carregar(); }) : () => { pagina = 1; carregar(); }));
  }
  const valoresFiltros = () => Object.fromEntries([...el.querySelectorAll('#filtros [data-f]')].map(c => [c.dataset.f, c.value]));

  async function carregar() {
    const r = RELATORIOS[atual];
    ultimaConsulta = {...valoresFiltros()};
    try {
      const t = await api(r.rota + qs({...ultimaConsulta, pagina, tamanho: 15}));
      el.querySelector('#resumo').innerHTML = t.resumo
        ? Object.entries(t.resumo).map(([k, v]) => `<div>${NOMES_RESUMO[k] ?? k}<b>${v === null ? '—' : fmt(v)}</b></div>`).join('') : '';
      el.querySelector('#tab').innerHTML = t.itens.length
        ? '<tr>' + t.colunas.map(c => `<th>${esc(c.titulo)}</th>`).join('') + '</tr>' +
          t.itens.map(i => '<tr>' + t.colunas.map(c => `<td>${celula(i[c.chave], c.tipo)}</td>`).join('') + '</tr>').join('')
        : '<tr><td class="empty">Nenhum dado para este filtro.</td></tr>';
      paginador(el.querySelector('#pag'), {pagina: t.pagina, tamanho: t.tamanho, total: t.total}, p => { pagina = p; carregar(); });
    } catch (e) { toast(e.message, 'err'); }
  }

  function escolher(k) {
    atual = k; pagina = 1;
    el.querySelectorAll('#abas button').forEach(b => b.classList.toggle('ativa', b.dataset.k === k));
    desenharFiltros(); carregar();
  }
  el.querySelector('#abas').addEventListener('click', e => { const b = e.target.closest('button[data-k]'); if (b) escolher(b.dataset.k); });
  el.querySelector('#csv').onclick = async () => {
    try { await baixarCsv(RELATORIOS[atual].rota, valoresFiltros(), 'relatorio-' + atual); toast('CSV gerado', 'ok'); }
    catch (e) { toast(e.message, 'err'); }
  };

  [obras, materiais] = await Promise.all([eng ? Promise.resolve([]) : api('/obras'), api('/materiais')]);
  escolher(atual);
}
