import {api, apiJson, comBotao, dataHora, esc, formJson, toast, $, PERFIL} from '../api.js';

export const titulo = 'Usuários';
export const perfis = ['gestor'];

export async function montar(el, usuario) {
  let lista = [], obras = [], editandoId = null, senhaId = null;

  const opcoesObra = () => '<option value="">— nenhuma —</option>' +
    obras.map(o => `<option value="${o.id}">${esc(o.nome)} — ${esc(o.cidade)}</option>`).join('');
  const opcoesPerfil = Object.entries(PERFIL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');

  el.innerHTML = `<h1 class="pagina-titulo">Usuários</h1>
    <section><h2>Novo usuário</h2>
      <form id="fUser" autocomplete="off">
        <label>Nome<input name="nome" required maxlength="120"></label>
        <label>E-mail<input name="email" type="email" required autocomplete="off"></label>
        <label>Senha inicial (mín. 8 caracteres)<input name="senha" type="password" minlength="8" required autocomplete="new-password"></label>
        <label>Perfil<select name="perfil">${opcoesPerfil}</select></label>
        <label class="full" id="lObra">Obra da equipe (obrigatória para engenheiro)<select name="obra_id"></select></label>
        <button class="full" type="submit">Cadastrar usuário</button>
      </form></section>
    <section><h2>Usuários cadastrados</h2><div class="tw"><table id="tab"></table></div></section>

    <dialog id="dlgEd"><form id="fEd" method="dialog">
      <h2 id="edTitulo">Editar usuário</h2>
      <label>Nome<input name="nome" required maxlength="120"></label>
      <label>Perfil<select name="perfil">${opcoesPerfil}</select></label>
      <label id="edLObra">Obra<select name="obra_id"></select></label>
      <label class="chk"><input name="ativo" type="checkbox"> Usuário ativo</label>
      <div class="linha"><button type="submit" class="grow">Salvar</button><button type="button" class="sm" data-fechar>Cancelar</button></div>
    </form></dialog>

    <dialog id="dlgSenha"><form id="fSenha" method="dialog">
      <h2 id="senhaTitulo">Redefinir senha</h2>
      <label>Nova senha (mín. 8 caracteres)<input name="nova_senha" type="password" minlength="8" required autocomplete="new-password"></label>
      <div class="linha"><button type="submit" class="grow">Redefinir</button><button type="button" class="sm" data-fechar>Cancelar</button></div>
    </form></dialog>`;

  const fUser = $('#fUser', el), fEd = $('#fEd', el), fSenha = $('#fSenha', el);
  const nomeObra = id => obras.find(o => o.id === id)?.nome ?? '—';

  // a obra só faz sentido para engenheiro
  const sincObra = (form, rotulo) => { rotulo.hidden = form.perfil.value !== 'engenheiro'; };
  fUser.perfil.addEventListener('change', () => sincObra(fUser, $('#lObra', el)));
  fEd.perfil.addEventListener('change', () => sincObra(fEd, $('#edLObra', el)));

  function renderTabela() {
    $('#tab', el).innerHTML = lista.length
      ? `<tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Obra</th><th>Situação</th><th>Criado em</th><th></th></tr>` + lista.map(u => `
        <tr><td>${esc(u.nome)}</td><td>${esc(u.email)}</td><td>${PERFIL[u.perfil]}</td>
        <td>${u.obra_id ? esc(nomeObra(u.obra_id)) : '—'}</td>
        <td><span class="badge s-${u.ativo ? 'ativo' : 'inativo'}">${u.ativo ? 'Ativo' : 'Inativo'}</span></td>
        <td>${dataHora(u.criado_em)}</td>
        <td><button class="sm" data-ed="${u.id}">Editar</button> <button class="sm" data-senha="${u.id}">Redefinir senha</button></td></tr>`).join('')
      : '<tr><td class="empty">Nenhum usuário.</td></tr>';
  }
  async function carregar() {
    [lista, obras] = await Promise.all([api('/usuarios'), api('/obras')]);
    fUser.obra_id.innerHTML = fEd.obra_id.innerHTML = opcoesObra();
    sincObra(fUser, $('#lObra', el));
    renderTabela();
  }

  fUser.addEventListener('submit', async e => {
    e.preventDefault();
    await comBotao($('[type=submit]', fUser), async () => {
      try {
        const corpo = formJson(fUser);
        if (corpo.perfil !== 'engenheiro') corpo.obra_id = null;
        await apiJson('/usuarios', 'POST', corpo);
        toast('Usuário cadastrado', 'ok'); fUser.reset(); await carregar();
      } catch (err) { toast(err.message, 'err'); }
    });
  });

  fEd.addEventListener('submit', async () => {
    try {
      const c = formJson(fEd);
      await apiJson(`/usuarios/${editandoId}`, 'PATCH', {
        nome: c.nome, perfil: c.perfil, ativo: c.ativo, obra_id: c.perfil === 'engenheiro' ? c.obra_id : null,
      });
      toast('Usuário atualizado', 'ok'); await carregar();
    } catch (err) { toast(err.message, 'err'); }
  });

  fSenha.addEventListener('submit', async () => {
    try {
      await apiJson(`/usuarios/${senhaId}/redefinir-senha`, 'POST', {nova_senha: fSenha.nova_senha.value});
      toast('Senha redefinida', 'ok');
    } catch (err) { toast(err.message, 'err'); }
    fSenha.reset();
  });

  el.addEventListener('click', e => {
    if (e.target.closest('[data-fechar]')) e.target.closest('dialog').close();
    const bEd = e.target.closest('[data-ed]'), bSenha = e.target.closest('[data-senha]');
    if (bEd) {
      const u = lista.find(x => x.id === Number(bEd.dataset.ed));
      editandoId = u.id;
      $('#edTitulo', el).textContent = 'Editar — ' + u.email;
      fEd.nome.value = u.nome; fEd.perfil.value = u.perfil; fEd.obra_id.value = u.obra_id ?? '';
      fEd.ativo.checked = u.ativo;
      sincObra(fEd, $('#edLObra', el));
      $('#dlgEd', el).showModal();
    }
    if (bSenha) {
      const u = lista.find(x => x.id === Number(bSenha.dataset.senha));
      senhaId = u.id;
      $('#senhaTitulo', el).textContent = 'Redefinir senha — ' + u.email;
      $('#dlgSenha', el).showModal();
    }
  });

  await carregar();
}
