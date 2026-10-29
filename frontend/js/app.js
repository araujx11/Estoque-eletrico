// Layout base + roteador (hash). Cada página exporta {titulo, perfis, montar(el, usuario) -> função de limpeza}.
import {api, getToken, getUsuario, salvarSessao, sair, $, esc, PERFIL, toast} from './api.js';
import * as dashboard from './pages/dashboard.js';
import * as materiais from './pages/materiais.js';
import * as obras from './pages/obras.js';
import * as requisicoes from './pages/requisicoes.js';
import * as usuarios from './pages/usuarios.js';
import * as diagnostico from './pages/diagnostico.js';
import * as movimentacoes from './pages/movimentacoes.js';
import * as relatorios from './pages/relatorios.js';
import * as auditoria from './pages/auditoria.js';

// a ordem aqui é a ordem do menu
const ROTAS = {dashboard, diagnostico, materiais, movimentacoes, obras, requisicoes, relatorios, usuarios, auditoria};
let limpar = null, navegacao = 0;

async function iniciar() {
  if (!getToken()) return location.replace('/login');
  let usuario;
  try {
    usuario = await api('/auth/me');          // revalida o token e atualiza perfil/obra
    salvarSessao(getToken(), usuario);
  } catch (e) {
    if (!getToken()) return;                  // 401: api.js já redirecionou
    document.body.innerHTML = `<p style="padding:24px">${esc(e.message)}</p>`;
    return;
  }

  $('#uNome').textContent = usuario.nome;
  $('#uPerfil').textContent = PERFIL[usuario.perfil];
  $('#btnSair').onclick = sair;
  $('#btnMenu').onclick = () => $('#side').classList.toggle('aberto');

  // só mostra no menu o que o perfil pode usar (o backend valida de qualquer forma)
  const permitidas = Object.entries(ROTAS).filter(([, p]) => p.perfis.includes(usuario.perfil));
  $('#menu').innerHTML = permitidas.map(([id, p]) => `<a href="#/${id}" data-rota="${id}">${esc(p.titulo)}</a>`).join('');
  $('#shell').hidden = false;

  async function navegar() {
    const [id, consulta] = location.hash.replace('#/', '').split('?');   // ex.: #/movimentacoes?entrada=3
    const rota = permitidas.find(([r]) => r === id) ? id : 'dashboard';
    if (id !== rota) return (location.hash = '#/' + rota);
    limpar?.(); limpar = null;
    $('#side').classList.remove('aberto');
    for (const a of document.querySelectorAll('#menu a')) a.classList.toggle('ativo', a.dataset.rota === rota);
    // container novo por página: os listeners da página anterior vão embora junto com ele
    const el = document.createElement('div');
    el.className = 'page';
    $('#pagina').replaceChildren(el);
    const minha = ++navegacao;
    try {
      const fn = await ROTAS[rota].montar(el, usuario, new URLSearchParams(consulta || ''));
      if (minha === navegacao) limpar = fn; else fn?.();   // usuário já navegou durante o carregamento
    } catch (e) { toast(e.message, 'err'); }
  }
  window.addEventListener('hashchange', navegar);
  navegar();
}

iniciar();
