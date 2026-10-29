import {api, apiJson, comBotao, esc, fmt, hora, dataHora, toast, $, $$, STATUS} from '../api.js';

export const titulo = 'Requisições';
export const perfis = ['gestor', 'almoxarife', 'engenheiro'];

const POLL_MS = 3000;

export async function montar(el, usuario) {
  const podePedir = usuario.perfil !== 'almoxarife';
  let materiais = [], obras = [], reqs = [], ultimo = {}, primeira = true;

  el.innerHTML = `<h1 class="pagina-titulo">Requisições</h1>
    ${podePedir ? `<section><h2>Nova requisição</h2>
      <form id="fReq" style="grid-template-columns:1fr">
        <label>Obra<select id="selObra" required></select></label>
        <div id="itens"></div>
        <div class="linha">
          <button type="button" class="sm" id="addItem">+ Adicionar material</button>
          <button type="submit" style="margin-left:auto">Enviar pedido para a fila</button>
        </div>
      </form></section>` : ''}
    <section>
      <div class="linha" style="margin-bottom:10px">
        <h2 style="margin:0" class="grow">Pedidos <small class="mut" style="font-weight:400">(atualiza sozinho a cada ${POLL_MS / 1000}s)</small></h2>
        <input id="busca" type="search" placeholder="Buscar obra, material ou worker…" aria-label="Buscar requisições">
        <select id="filtroObra" aria-label="Filtrar por obra" ${usuario.perfil === 'engenheiro' ? 'hidden' : ''}><option value="">Todas as obras</option></select>
        <label class="linha" style="display:flex">Status
          <select id="filtro"><option value="">Todos</option>
            ${Object.entries(STATUS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
      </div>
      <div class="tw"><table id="tab"></table></div>
    </section>`;

  const matDe = id => materiais.find(m => m.id === id);
  const nomeObra = id => obras.find(o => o.id === id)?.nome ?? '#' + id;

  function renderTabela() {
    const filtro = $('#filtro', el).value, obraSel = $('#filtroObra', el).value;
    const termo = $('#busca', el).value.trim().toLowerCase();
    const itens = reqs.filter(r => (!filtro || r.status === filtro) && (!obraSel || String(r.obra_id) === obraSel)
      && (!termo || `${nomeObra(r.obra_id)} ${matDe(r.material_id)?.nome ?? ''} ${r.worker_id ?? ''}`.toLowerCase().includes(termo))).slice(0, 100);
    $('#tab', el).innerHTML = itens.length
      ? `<tr><th>Status</th><th>Pedido</th><th>Obra</th><th>Material</th><th>Solicitado</th><th>Atendido</th><th>Worker</th><th>Criado</th><th>Processado</th></tr>` +
        itens.map(r => {
          const m = matDe(r.material_id), un = m?.unidade ?? '';
          return `<tr><td><span class="badge s-${r.status}">${STATUS[r.status]}</span></td>
            <td class="pedido">${r.pedido_id ? '#' + r.pedido_id.slice(0, 6) : '—'}</td>
            <td>${esc(nomeObra(r.obra_id))}</td><td>${esc(m?.nome ?? '#' + r.material_id)}</td>
            <td>${fmt(r.quantidade_solicitada)} ${esc(un)}</td><td>${fmt(r.quantidade_atendida)} ${esc(un)}</td>
            <td>${esc(r.worker_id ?? '—')}</td><td>${dataHora(r.criado_em)}</td><td>${hora(r.processado_em)}</td></tr>`;
        }).join('')
      : '<tr><td class="empty">Nenhuma requisição.</td></tr>';
  }

  function avisarMudancas() {
    for (const r of reqs) {
      const antes = ultimo[r.id];
      if (!primeira && antes && antes !== r.status && !['pendente', 'em_processamento'].includes(r.status)) {
        const tipo = r.status === 'aprovada' ? 'ok' : r.status === 'parcial' ? '' : 'err';
        toast(`${nomeObra(r.obra_id)} · ${matDe(r.material_id)?.nome ?? ''}: ${STATUS[r.status].toUpperCase()} (${fmt(r.quantidade_atendida)}/${fmt(r.quantidade_solicitada)})`, tipo);
      }
      ultimo[r.id] = r.status;
    }
    primeira = false;
  }

  // ---- formulário de pedido (vários materiais) ----
  function renderObras() {
    const sel = $('#selObra', el);
    if (!sel) return;
    const atual = sel.value;
    sel.innerHTML = obras.filter(o => o.status !== 'finalizada')
      .map(o => `<option value="${o.id}">${esc(o.nome)} — ${esc(o.cidade)}</option>`).join('');
    if (atual) sel.value = atual;
  }
  function novoItem() {
    const d = document.createElement('div');
    d.className = 'item';
    d.innerHTML = `<label>Material<select class="mat" required></select></label>
      <label>Quantidade<span class="q"><input class="qtd" type="number" min="0.001" step="any" required><span class="un"></span></span></label>
      <button type="button" class="sm del rm" title="Remover item">✕</button><div class="hint"></div>`;
    d.querySelector('.mat').addEventListener('change', syncItens);
    d.querySelector('.rm').onclick = () => { d.remove(); syncItens(); };
    $('#itens', el).append(d);
    syncItens();
  }
  function syncItens() {
    const linhas = $$('#itens .item', el);
    const usados = linhas.map(l => l.querySelector('.mat').value);
    linhas.forEach(l => {
      const sel = l.querySelector('.mat'), atual = sel.value;
      const livres = materiais.filter(m => String(m.id) === atual || !usados.includes(String(m.id)));
      sel.innerHTML = livres.map(m => `<option value="${m.id}">${esc(m.nome)}</option>`).join('');
      if (atual && livres.some(m => String(m.id) === atual)) sel.value = atual;
      const m = matDe(Number(sel.value));
      l.querySelector('.un').textContent = m?.unidade ?? '';
      l.querySelector('.hint').textContent = m ? `Disponível: ${fmt(m.quantidade_estoque)} ${m.unidade}` : '';
      l.querySelector('.rm').style.visibility = linhas.length > 1 ? 'visible' : 'hidden';
    });
  }

  if (podePedir) {
    $('#addItem', el).onclick = novoItem;
    $('#fReq', el).addEventListener('submit', async e => {
      e.preventDefault();
      const itens = $$('#itens .item', el).map(l => ({
        material_id: Number(l.querySelector('.mat').value),
        quantidade_solicitada: Number(l.querySelector('.qtd').value),
      }));
      await comBotao($('[type=submit]', e.target), async () => {
        try {
          await apiJson('/pedidos', 'POST', {obra_id: Number($('#selObra', el).value), itens});
          toast(`Pedido com ${itens.length} item(ns) enviado para a fila`, 'ok');
          $('#itens', el).innerHTML = ''; novoItem(); await atualizar();
        } catch (err) { toast(err.message, 'err'); }
      });
    });
  }
  $('#filtro', el).addEventListener('change', renderTabela);
  $('#filtroObra', el).addEventListener('change', renderTabela);
  $('#busca', el).addEventListener('input', renderTabela);

  async function atualizar() {
    try {
      [materiais, obras, reqs] = await Promise.all([api('/materiais'), api('/obras'), api('/requisicoes')]);
      if ($('#filtroObra', el).options.length === 1) {   // preenche o filtro de obra uma única vez
        $('#filtroObra', el).insertAdjacentHTML('beforeend', obras.map(o => `<option value="${o.id}">${esc(o.nome)}</option>`).join(''));
      }
      renderObras(); syncItens(); renderTabela(); avisarMudancas();
    } catch (e) { /* falha momentânea de rede: tenta de novo no próximo ciclo */ }
  }

  await atualizar();
  if (podePedir) novoItem();
  const timer = setInterval(atualizar, POLL_MS);
  return () => clearInterval(timer);
}
