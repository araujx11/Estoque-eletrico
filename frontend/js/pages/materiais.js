import {api, apiJson, carregarUnidades, comBotao, confirmar, esc, fmt, formJson, toast, $} from '../api.js';

export const titulo = 'Materiais';
export const perfis = ['gestor', 'almoxarife', 'engenheiro'];

export async function montar(el, usuario) {
  const podeEditar = usuario.perfil !== 'engenheiro';
  const podeRemover = usuario.perfil === 'gestor';
  let lista = [], editando = null;

  el.innerHTML = `<h1 class="pagina-titulo">Materiais</h1>
    <div class="grid2">
      ${podeEditar ? `<section><h2 id="fTitulo">Novo material</h2>
        <form id="fMat">
          <label class="full">Nome<input name="nome" required maxlength="120"></label>
          <label>Quantidade em estoque<input name="quantidade_estoque" type="number" min="0" step="any" required></label>
          <label>Unidade<select name="unidade" id="selUn"></select></label>
          <label>Estoque crítico<input name="estoque_critico" type="number" min="0" step="any" value="0"></label>
          <div class="full linha"><button class="grow" type="submit">Salvar</button><button type="button" class="sm" id="cancelar" hidden>Cancelar edição</button></div>
        </form></section>` : ''}
      <section style="grid-column:1/-1">
        <div class="linha" style="margin-bottom:10px"><h2 style="margin:0" class="grow">Estoque</h2>
          ${podeRemover ? '<label class="chk"><input id="verInativos" type="checkbox"> Mostrar desativados</label>' : ''}
          <input id="busca" type="search" placeholder="Buscar por nome…" aria-label="Buscar material"></div>
        <div class="tw"><table id="tab"></table></div>
      </section>
    </div>`;

  if (podeEditar) await carregarUnidades($('#selUn', el));

  function renderTabela() {
    const termo = $('#busca', el).value.trim().toLowerCase();
    const itens = lista.filter(m => m.nome.toLowerCase().includes(termo));
    $('#tab', el).innerHTML = itens.length
      ? `<tr><th>Material</th><th>Estoque</th><th>Crítico</th><th>Nível</th><th></th></tr>` + itens.map(m => {
        const crit = m.quantidade_estoque <= m.estoque_critico;
        const max = Math.max(m.quantidade_estoque, m.estoque_critico * 3, 1);
        const off = m.ativo === false;
        return `<tr class="${off ? 'inativo' : (crit ? 'critico' : '')}"><td>${esc(m.nome)}${off ? ' <span class="badge">desativado</span>' : ''}</td>
          <td>${fmt(m.quantidade_estoque)} ${esc(m.unidade)}</td><td>${fmt(m.estoque_critico)} ${esc(m.unidade)}</td>
          <td><div class="bar ${crit ? 'crit' : ''}" title="${crit ? 'Abaixo do crítico' : 'OK'}"><i style="width:${m.quantidade_estoque / max * 100}%"></i></div></td>
          <td>${podeEditar && !off ? `<button class="sm" data-editar="${m.id}">Editar</button>
                <a class="btnlink" href="#/movimentacoes?entrada=${m.id}">+ Entrada</a>` : ''}
              ${podeRemover ? (off ? `<button class="sm" data-reativar="${m.id}">Reativar</button>`
                                   : `<button class="sm del" data-remover="${m.id}">Desativar</button>`) : ''}</td></tr>`;
      }).join('')
      : '<tr><td class="empty">Nenhum material encontrado.</td></tr>';
  }

  async function carregar() {
    const inativos = podeRemover && $('#verInativos', el)?.checked;
    lista = await api('/materiais' + (inativos ? '?incluir_inativos=true' : '')); renderTabela();
  }

  function entrarEdicao(m) {
    editando = m;
    const f = $('#fMat', el);
    f.nome.value = m.nome; f.quantidade_estoque.value = m.quantidade_estoque;
    f.unidade.value = m.unidade; f.estoque_critico.value = m.estoque_critico;
    // o saldo só muda por movimentação (entrada/ajuste), que registra quem, quando e por quê
    f.quantidade_estoque.readOnly = true;
    f.quantidade_estoque.title = 'Para alterar o estoque, registre uma entrada ou ajuste em Movimentações';
    $('#fTitulo', el).textContent = 'Editar material';
    $('#cancelar', el).hidden = false;
    f.nome.focus();
  }
  function sairEdicao() {
    editando = null;
    $('#fMat', el).reset();
    $('#fMat', el).quantidade_estoque.readOnly = false;
    $('#fMat', el).quantidade_estoque.title = '';
    $('#fTitulo', el).textContent = 'Novo material';
    $('#cancelar', el).hidden = true;
  }

  $('#busca', el).addEventListener('input', renderTabela);
  $('#verInativos', el)?.addEventListener('change', () => carregar().catch(e => toast(e.message, 'err')));

  if (podeEditar) {
    $('#cancelar', el).onclick = sairEdicao;
    $('#fMat', el).addEventListener('submit', async e => {
      e.preventDefault();
      await comBotao($('[type=submit]', e.target), async () => {
        try {
          const corpo = formJson(e.target);
          if (editando) await apiJson(`/materiais/${editando.id}`, 'PUT', corpo);
          else await apiJson('/materiais', 'POST', corpo);
          toast(editando ? 'Material atualizado' : 'Material cadastrado', 'ok');
          sairEdicao(); await carregar();
        } catch (err) { toast(err.message, 'err'); }
      });
    });
  }

  el.addEventListener('click', async e => {
    const bEd = e.target.closest('[data-editar]'), bRm = e.target.closest('[data-remover]'), bRe = e.target.closest('[data-reativar]');
    if (bEd) {
      entrarEdicao(lista.find(m => m.id === Number(bEd.dataset.editar)));
      scrollTo({top: 0, behavior: 'smooth'});
    }
    if (bRm) {
      const m = lista.find(x => x.id === Number(bRm.dataset.remover));
      if (!await confirmar(`Desativar "${m.nome}"? O histórico é mantido e você pode reativá-lo depois.`, 'Desativar')) return;
      try { await api(`/materiais/${m.id}`, {method: 'DELETE'}); toast('Material desativado', 'ok'); await carregar(); }
      catch (err) { toast(err.message, 'err'); }
    }
    if (bRe) {
      try { await apiJson(`/materiais/${bRe.dataset.reativar}`, 'PATCH', {ativo: true}); toast('Material reativado', 'ok'); await carregar(); }
      catch (err) { toast(err.message, 'err'); }
    }
  });

  await carregar();
}
