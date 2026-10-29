import {api, esc, fmt, fmtData, qs, toast, STATUS} from '../api.js';

export const titulo = 'Dashboard';
export const perfis = ['gestor', 'almoxarife', 'engenheiro'];

const card = (rotulo, valor, cls = '', dica = '') =>
  `<div class="stat ${cls}" title="${esc(dica)}"><span>${rotulo}</span><b>${valor}</b></div>`;

// cores vêm das variáveis CSS (funciona nos temas claro e escuro)
const cor = nome => getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
const CORES_STATUS = () => ({pendente: cor('--neu'), em_processamento: cor('--info'), aprovada: cor('--ok'),
  parcial: cor('--warn'), negada: cor('--err'), erro: '#8e44ad'});

export async function montar(el, usuario) {
  const eng = usuario.perfil === 'engenheiro';
  const podeEntrada = !eng;
  let periodo = '30', graficos = {};

  el.innerHTML = `<div class="linha"><h1 class="pagina-titulo grow">Dashboard${eng ? ' — minha obra' : ''}</h1>
      <label class="linha" style="display:flex">Período
        <select id="periodo"><option value="7">Últimos 7 dias</option><option value="30" selected>Últimos 30 dias</option><option value="todos">Tudo</option></select></label></div>
    <div class="cards" id="kpi"></div>
    <div class="charts">
      <section><h2>Requisições por dia</h2><div class="chart-box"><canvas id="gDia"></canvas></div></section>
      <section><h2>Distribuição por status</h2><div class="chart-box"><canvas id="gStatus"></canvas></div></section>
      <section><h2>Top 5 materiais mais consumidos</h2><div class="chart-box"><canvas id="gTop"></canvas></div></section>
      <section><h2>Itens atendidos por obra</h2><div class="chart-box"><canvas id="gObra"></canvas></div></section>
    </div>
    <section><h2>⚠️ Alertas de estoque crítico</h2><div id="alertas" class="alerta-lista"></div></section>`;

  const semChart = typeof Chart === 'undefined';
  if (semChart) {
    el.querySelectorAll('.chart-box').forEach(b => b.innerHTML =
      '<div class="sem-grafico">Gráficos indisponíveis: não foi possível carregar o Chart.js (CDN). Os números acima continuam válidos.</div>');
  } else {
    Chart.defaults.color = cor('--mut');
    Chart.defaults.borderColor = cor('--bd');
    Chart.defaults.font.family = 'system-ui, sans-serif';
  }

  // cria o gráfico na 1ª vez; depois só troca os dados (sem piscar)
  function grafico(id, tipo, dados, opcoes = {}) {
    if (semChart) return;
    if (graficos[id]) { graficos[id].data = dados; graficos[id].update('none'); return; }
    graficos[id] = new Chart(el.querySelector('#' + id), {
      type: tipo, data: dados,
      options: {responsive: true, maintainAspectRatio: false, ...opcoes},
    });
  }

  function desenhar(d) {
    const c = d.cards;
    el.querySelector('#kpi').innerHTML =
      card('Total de materiais', c.total_materiais, 'pri') +
      card('Materiais em alerta', c.materiais_alerta, c.materiais_alerta ? 'alerta' : 'pri', 'Estoque igual ou abaixo do crítico') +
      card('Requisições abertas', c.requisicoes_abertas, 'pri', 'Pendentes + em processamento') +
      card('Taxa de atendimento', c.taxa_atendimento_pct === null ? '—' : c.taxa_atendimento_pct.toLocaleString('pt-BR') + '%', 'aprovada',
           'Pedidos atendidos por completo, entre os já resolvidos no período') +
      (c.materiais_em_deficit === null ? '' :
        card('Materiais em déficit', c.materiais_em_deficit, c.materiais_em_deficit ? 'alerta' : 'pri',
             'Demanda aberta maior que o estoque (veja o Diagnóstico). Pedidos do período não atendidos por completo: ' + c.requisicoes_incompletas_periodo));

    grafico('gDia', 'line', {
      labels: d.por_dia.map(x => fmtData(x.data).slice(0, 5)),
      datasets: [{label: 'Requisições', data: d.por_dia.map(x => x.total), borderColor: cor('--pri'),
                  backgroundColor: cor('--pri') + '33', fill: true, tension: .25, pointRadius: 2}],
    }, {plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true, ticks: {precision: 0}}}});

    const cs = CORES_STATUS(), ks = Object.keys(STATUS);
    grafico('gStatus', 'doughnut', {
      labels: ks.map(k => STATUS[k]), datasets: [{data: ks.map(k => d.por_status[k] ?? 0), backgroundColor: ks.map(k => cs[k]), borderWidth: 0}],
    }, {plugins: {legend: {position: 'right'}}});

    grafico('gTop', 'bar', {
      labels: d.top_materiais.map(m => `${m.material} (${m.unidade})`),
      datasets: [{label: 'Quantidade atendida', data: d.top_materiais.map(m => m.quantidade), backgroundColor: cor('--pri')}],
    }, {indexAxis: 'y', plugins: {legend: {display: false}}, scales: {x: {beginAtZero: true}}});

    grafico('gObra', 'bar', {
      labels: d.consumo_por_obra.map(o => o.obra),
      datasets: [{label: 'Itens atendidos', data: d.consumo_por_obra.map(o => o.itens_atendidos), backgroundColor: cor('--ok')}],
    }, {plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true, ticks: {precision: 0}}}});

    el.querySelector('#alertas').innerHTML = d.alertas.length ? d.alertas.map(a => `
      <div class="alerta-item"><b>${esc(a.nome)}</b>
        <span>estoque ${fmt(a.estoque)} ${esc(a.unidade)} · crítico ${fmt(a.critico)} ${esc(a.unidade)}${a.falta_para_critico > 0 ? ` · faltam ${fmt(a.falta_para_critico)} para o crítico` : ''}</span>
        ${podeEntrada ? `<a href="#/movimentacoes?entrada=${a.material_id}" style="margin-left:auto">Registrar entrada →</a>` : ''}</div>`).join('')
      : '<div class="empty">✅ Nenhum material em estoque crítico.</div>';
  }

  async function atualizar() {
    try { desenhar(await api('/dashboard/completo' + qs({periodo}))); }
    catch (e) { toast(e.message, 'err'); }
  }

  el.querySelector('#periodo').onchange = e => { periodo = e.target.value; atualizar(); };
  await atualizar();
  const timer = setInterval(atualizar, 10000);
  return () => { clearInterval(timer); Object.values(graficos).forEach(g => g.destroy()); };
}
