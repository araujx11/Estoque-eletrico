import {api, apiJson, comBotao, confirmar, dataHora, esc, formJson, toast, $} from '../api.js';

export const titulo = 'Obras';
export const perfis = ['gestor', 'almoxarife'];

export async function montar(el, usuario) {
  const gestor = usuario.perfil === 'gestor';
  let lista = [], obraAnd = null;

  el.innerHTML = `<h1 class="pagina-titulo">Obras</h1>
    ${gestor ? `<section><h2>Nova obra</h2>
      <form id="fObra">
        <label>Nome<input name="nome" required maxlength="120"></label>
        <label>Cidade<input name="cidade" required maxlength="80"></label>
        <label>Prioridade (1 = mais urgente)
          <select name="nivel_prioridade"><option>1</option><option>2</option><option>3</option><option>4</option><option selected>5</option></select></label>
        <label>Prazo de entrega<input name="prazo_entrega" type="datetime-local"></label>
        <label class="chk full"><input name="parada_por_falta_material" type="checkbox"> Obra parada por falta de material</label>
        <button class="full" type="submit">Cadastrar obra</button>
      </form></section>` : ''}
    <section><h2>Obras cadastradas</h2>
      <div class="filtros"><label>Buscar<input id="busca" type="search" placeholder="nome ou cidade"></label>
        <label>Status<select id="fStatus"><option value="">Todos</option><option value="em_andamento">Em andamento</option><option value="finalizada">Finalizadas</option></select></label></div>
      <div class="tw"><table id="tab"></table></div></section>
    <dialog id="dlg">
      <form id="fAnd" method="dialog">
        <h2 id="dlgTitulo">Andamento</h2>
        <label>Progresso: <b id="progVal">0</b>%<input name="progresso" type="range" min="0" max="100" step="5"></label>
        <label>Descrição do andamento<textarea name="descricao_andamento" maxlength="1000" placeholder="Ex.: Cabeamento do 2º pavimento concluído"></textarea></label>
        <label class="chk"><input name="finalizada" type="checkbox"> Marcar obra como finalizada</label>
        <div class="linha"><button type="submit" class="grow">Salvar</button><button type="button" class="sm" id="dlgCancel">Cancelar</button></div>
      </form>
    </dialog>`;

  function renderTabela() {
    const termo = $('#busca', el).value.trim().toLowerCase(), st = $('#fStatus', el).value;
    const vis = lista.filter(o => (!st || o.status === st) && (o.nome + ' ' + o.cidade).toLowerCase().includes(termo));
    $('#tab', el).innerHTML = vis.length
      ? `<tr><th>Obra</th><th>Cidade</th><th>Status</th><th>Andamento</th><th>Prio.</th><th>Prazo</th><th>Parada</th><th></th></tr>` + vis.map(o => `
        <tr><td>${esc(o.nome)}</td><td>${esc(o.cidade)}</td>
        <td><span class="badge s-${o.status}">${o.status === 'finalizada' ? 'Finalizada' : 'Em andamento'}</span>
          ${o.finalizada_em ? `<div class="desc">${dataHora(o.finalizada_em)}</div>` : ''}</td>
        <td><div class="bar" title="${o.progresso}%"><i style="width:${o.progresso}%"></i></div>${o.progresso}%
          ${o.descricao_andamento ? `<div class="desc">${esc(o.descricao_andamento)}</div>` : ''}</td>
        <td>${o.nivel_prioridade}</td><td>${dataHora(o.prazo_entrega)}</td>
        <td>${o.parada_por_falta_material ? '⚠️ sim' : 'não'}</td>
        <td>${gestor ? `<button class="sm" data-and="${o.id}">Andamento</button>
          <button class="sm del" data-rm="${o.id}">Remover</button>` : ''}</td></tr>`).join('')
      : '<tr><td class="empty">Nenhuma obra cadastrada.</td></tr>';
  }
  async function carregar() { lista = await api('/obras'); renderTabela(); }
  $('#busca', el).addEventListener('input', renderTabela);
  $('#fStatus', el).addEventListener('change', renderTabela);

  if (gestor) {
    $('#fObra', el).addEventListener('submit', async e => {
      e.preventDefault();
      await comBotao($('[type=submit]', e.target), async () => {
        try {
          const corpo = formJson(e.target);
          if (corpo.prazo_entrega) corpo.prazo_entrega = new Date(corpo.prazo_entrega).toISOString();
          await apiJson('/obras', 'POST', corpo);
          toast('Obra cadastrada', 'ok'); e.target.reset(); await carregar();
        } catch (err) { toast(err.message, 'err'); }
      });
    });

    const f = $('#fAnd', el), dlg = $('#dlg', el);
    f.progresso.addEventListener('input', ev => $('#progVal', el).textContent = ev.target.value);
    f.finalizada.addEventListener('change', ev => {
      f.descricao_andamento.disabled = ev.target.checked;
      if (ev.target.checked) { f.progresso.value = 100; $('#progVal', el).textContent = 100; }
    });
    $('#dlgCancel', el).onclick = () => dlg.close();
    f.addEventListener('submit', async () => {
      try {
        await apiJson(`/obras/${obraAnd}/andamento`, 'PATCH', {
          progresso: Number(f.progresso.value),
          descricao_andamento: f.finalizada.checked ? null : (f.descricao_andamento.value || null),
          finalizada: f.finalizada.checked,
        });
        toast('Andamento atualizado', 'ok'); await carregar();
      } catch (err) { toast(err.message, 'err'); }
    });

    el.addEventListener('click', async e => {
      const bAnd = e.target.closest('[data-and]'), bRm = e.target.closest('[data-rm]');
      if (bAnd) {
        const o = lista.find(x => x.id === Number(bAnd.dataset.and));
        obraAnd = o.id;
        $('#dlgTitulo', el).textContent = 'Andamento — ' + o.nome;
        f.progresso.value = o.progresso; $('#progVal', el).textContent = o.progresso;
        f.descricao_andamento.value = o.descricao_andamento ?? '';
        f.finalizada.checked = o.status === 'finalizada';
        f.descricao_andamento.disabled = f.finalizada.checked;
        dlg.showModal();
      }
      if (bRm) {
        const o = lista.find(x => x.id === Number(bRm.dataset.rm));
        if (!await confirmar(`Remover a obra "${o.nome}"?`, 'Remover')) return;
        try {
          try { await api(`/obras/${o.id}`, {method: 'DELETE'}); }
          catch (err) {
            if (err.status !== 409 || !/histórico/.test(err.message) || !await confirmar(err.message, 'Remover tudo')) throw err;
            await api(`/obras/${o.id}?forcar=true`, {method: 'DELETE'});
          }
          toast('Obra removida', 'ok'); await carregar();
        } catch (err) { toast(err.message, 'err'); }
      }
    });
  }

  await carregar();
}
