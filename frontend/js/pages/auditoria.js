import {api, dataHora, esc, paginador, qs, toast, $} from '../api.js';

export const titulo = 'Auditoria';
export const perfis = ['gestor'];

export async function montar(el) {
  let pagina = 1;
  el.innerHTML = `<h1 class="pagina-titulo">Auditoria</h1>
    <section><div class="filtros">
        <label>Usuário<select id="fUsuario"><option value="">Todos</option></select></label>
        <label>Ação<select id="fAcao"><option value="">Todas</option></select></label>
        <label>Entidade<select id="fEntidade"><option value="">Todas</option>
          ${['usuario', 'material', 'obra', 'requisicao', 'pedido'].map(x => `<option>${x}</option>`).join('')}</select></label>
        <label>De<input id="fDe" type="date"></label><label>Até<input id="fAte" type="date"></label></div>
      <p class="mut" style="margin-top:0">Registro de ações relevantes. Senhas e tokens nunca são gravados.</p>
      <div class="tw"><table id="tab"></table></div><div id="pag"></div></section>`;

  async function carregar() {
    try {
      const r = await api('/auditoria' + qs({usuario_id: $('#fUsuario', el).value, acao: $('#fAcao', el).value,
        entidade: $('#fEntidade', el).value, de: $('#fDe', el).value, ate: $('#fAte', el).value, pagina, tamanho: 20}));
      $('#tab', el).innerHTML = r.itens.length
        ? '<tr><th>Quando</th><th>Quem</th><th>Ação</th><th>Entidade</th><th>Detalhes</th></tr>' + r.itens.map(l => `
          <tr><td>${dataHora(l.criado_em)}</td><td>${esc(l.usuario_nome ?? '—')}</td><td><b>${esc(l.acao)}</b></td>
          <td>${esc(l.entidade ?? '—')}${l.entidade_id ? ' #' + esc(String(l.entidade_id).slice(0, 8)) : ''}</td>
          <td>${l.detalhes ? `<details class="json"><summary>ver</summary><pre>${esc(JSON.stringify(l.detalhes, null, 2))}</pre></details>` : '—'}</td></tr>`).join('')
        : '<tr><td class="empty">Nenhum registro.</td></tr>';
      paginador($('#pag', el), {pagina: r.pagina, tamanho: r.tamanho, total: r.total}, p => { pagina = p; carregar(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  for (const id of ['#fUsuario', '#fAcao', '#fEntidade', '#fDe', '#fAte']) $(id, el).addEventListener('change', () => { pagina = 1; carregar(); });

  const [usuarios, acoes] = await Promise.all([api('/usuarios'), api('/auditoria/acoes')]);
  $('#fUsuario', el).insertAdjacentHTML('beforeend', usuarios.map(u => `<option value="${u.id}">${esc(u.nome)}</option>`).join(''));
  $('#fAcao', el).insertAdjacentHTML('beforeend', acoes.map(a => `<option>${esc(a)}</option>`).join(''));
  await carregar();
}
