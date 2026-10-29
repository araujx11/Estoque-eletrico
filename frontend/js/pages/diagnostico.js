import {api, apiJson, comBotao, esc, fmt, dataHora, toast, $} from '../api.js';

export const titulo = 'Diagnóstico de déficit';
export const perfis = ['gestor', 'almoxarife'];

const AUTO_MS = 5000;

export async function montar(el) {
  let dados = null, simulacao = null, obras = [], materiais = [], timer = null;

  el.innerHTML = `<h1 class="pagina-titulo">Diagnóstico de déficit</h1>
    <section>
      <p class="mut" style="margin-top:0">Simula como o estoque <b>atual</b> seria distribuído entre os pedidos abertos
        (pendentes e em processamento), na mesma ordem de prioridade da fila. É uma previsão: nada é gravado e novos pedidos podem chegar.</p>
      <div class="linha">
        <button id="atualizar" type="button">↻ Atualizar agora</button>
        <label class="chk"><input id="auto" type="checkbox" checked> Atualizar a cada ${AUTO_MS / 1000}s</label>
        <label class="chk"><input id="soAtencao" type="checkbox"> Só materiais com atenção (🔴 🟡)</label>
        <input id="busca" type="search" placeholder="Buscar material…" aria-label="Buscar material">
        <span class="mut grow" id="geradoEm" style="text-align:right"></span>
      </div>
    </section>

    <section><h2>E se…? (simulação hipotética)</h2>
      <form id="fSim" style="grid-template-columns:2fr 2fr 1fr auto;align-items:end">
        <label>Obra<select name="obra_id" id="simObra" required></select></label>
        <label>Material<select name="material_id" id="simMat" required></select></label>
        <label>Quantidade<input name="quantidade" type="number" min="0.001" step="any" required></label>
        <button type="submit">Simular</button>
      </form></section>
    <div id="bannerSim"></div>
    <div id="semaforos" class="diag-grid"></div>
    <section><h2>Déficit histórico (pedidos já resolvidos como parciais ou negados)</h2><div class="tw" id="historico"></div></section>`;

  const barra = m => {
    const maior = Math.max(m.estoque_atual, m.demanda_total, 1);
    return `<div class="barra2" role="img" aria-label="Estoque ${fmt(m.estoque_atual)} contra demanda ${fmt(m.demanda_total)} ${esc(m.unidade)}">
      <div class="est" style="width:${m.estoque_atual / maior * 100}%"></div><div class="dem" style="width:${m.demanda_total / maior * 100}%"></div></div>
      <div class="leg"><span>▮ Estoque: ${fmt(m.estoque_atual)} ${esc(m.unidade)}</span><span>▮ Demanda: ${fmt(m.demanda_total)} ${esc(m.unidade)}</span>
        <span>Crítico: ${fmt(m.estoque_critico)} ${esc(m.unidade)}</span></div>`;
  };
  const NOME_SEM = {verde: 'Estoque suficiente', amarelo: 'Atenção', vermelho: 'Déficit'};

  function desenhar() {
    const d = simulacao ?? dados;
    if (!d) return;
    const termo = $('#busca', el).value.trim().toLowerCase(), soAtencao = $('#soAtencao', el).checked;
    $('#geradoEm', el).textContent = 'Gerado em ' + dataHora(d.gerado_em);
    $('#bannerSim', el).innerHTML = simulacao
      ? `<div class="banner">🧪 <b>Simulação hipotética</b> — nada foi gravado. <button type="button" class="sm" id="voltar">Voltar ao diagnóstico real</button></div>` : '';
    if (simulacao) $('#voltar', el).onclick = () => { simulacao = null; desenhar(); };

    const lista = d.materiais.filter(m => m.nome.toLowerCase().includes(termo) && (!soAtencao || m.semaforo !== 'verde'));
    $('#semaforos', el).innerHTML = lista.length ? lista.map(m => `
      <section class="diag-card ${m.semaforo}">
        <h3><span aria-hidden="true">${m.icone}</span>${esc(m.nome)}
          <span class="sem-rotulo ${m.semaforo}">${m.icone} ${NOME_SEM[m.semaforo]}</span></h3>
        <p class="diag-msg">${esc(m.mensagem)}</p>${barra(m)}
        ${m.requisicoes.length ? `<div class="tw"><table><tr><th>#</th><th>Obra</th><th>Pediu</th><th>Receberia</th><th>Ficaria pendente</th><th>Situação</th></tr>` +
          m.requisicoes.map(r => `<tr class="${r.hipotetica ? 'hipo' : ''}"><td>${r.ordem}</td>
            <td>${esc(r.obra_nome)}${r.obra_parada ? ' ⚠️' : ''}<div class="desc">prioridade ${r.nivel_prioridade}${r.prazo_entrega ? ' · prazo ' + new Date(r.prazo_entrega + 'Z').toLocaleDateString('pt-BR') : ''}</div></td>
            <td>${fmt(r.quantidade_pedida)} ${esc(m.unidade)}</td><td>${fmt(r.quantidade_atendida)} ${esc(m.unidade)}</td>
            <td>${fmt(r.quantidade_pendente)} ${esc(m.unidade)}</td><td>${r.icone} ${esc(r.rotulo)}</td></tr>`).join('') + '</table></div>'
          : '<div class="empty">Sem pedidos abertos para este material.</div>'}
      </section>`).join('') : '<div class="empty">Nenhum material para mostrar com este filtro.</div>';

    $('#historico', el).innerHTML = d.deficit_historico.length
      ? `<table><tr><th>Material</th><th>Parciais</th><th>Negadas</th><th>Déficit acumulado</th></tr>` + d.deficit_historico.map(h =>
        `<tr><td>${esc(h.nome)}</td><td>${h.requisicoes_parciais}</td><td>${h.requisicoes_negadas}</td><td>${fmt(h.deficit_acumulado)} ${esc(h.unidade)}</td></tr>`).join('') + '</table>'
      : '<div class="empty">Nenhum déficit histórico.</div>';
  }

  async function carregar() {
    try { dados = await api('/diagnostico/deficit'); desenhar(); }
    catch (e) { toast(e.message, 'err'); }
  }
  function agendar() {
    clearInterval(timer);
    if ($('#auto', el).checked) timer = setInterval(() => { if (!simulacao) carregar(); }, AUTO_MS);
  }

  $('#atualizar', el).onclick = () => { simulacao = null; carregar(); };
  $('#auto', el).onchange = agendar;
  $('#soAtencao', el).onchange = desenhar;
  $('#busca', el).oninput = desenhar;
  $('#fSim', el).addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    await comBotao($('[type=submit]', f), async () => {
      try {
        simulacao = await apiJson('/diagnostico/simular', 'POST', {
          requisicoes: [{obra_id: Number(f.obra_id.value), material_id: Number(f.material_id.value), quantidade: Number(f.quantidade.value)}],
          material_id: Number(f.material_id.value)});
        desenhar();
      } catch (err) { toast(err.message, 'err'); }
    });
  });

  [obras, materiais] = await Promise.all([api('/obras'), api('/materiais')]);
  $('#simObra', el).innerHTML = obras.filter(o => o.status !== 'finalizada').map(o => `<option value="${o.id}">${esc(o.nome)} — ${esc(o.cidade)}</option>`).join('');
  $('#simMat', el).innerHTML = materiais.map(m => `<option value="${m.id}">${esc(m.nome)}</option>`).join('');
  await carregar();
  agendar();
  return () => clearInterval(timer);
}
