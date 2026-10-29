// API, sessão (token no localStorage) e utilitários compartilhados.
const KEY_TOKEN = 'estoque_token';
const KEY_USER = 'estoque_usuario';

export const STATUS = {pendente: 'Pendente', em_processamento: 'Processando', aprovada: 'Aprovada', parcial: 'Parcial', negada: 'Negada', erro: 'Erro'};
export const PERFIL = {gestor: 'Gestor', almoxarife: 'Almoxarife', engenheiro: 'Engenheiro'};

// ---- sessão ----
export const getToken = () => localStorage.getItem(KEY_TOKEN);
export function getUsuario() {
  try { return JSON.parse(localStorage.getItem(KEY_USER)); } catch { return null; }
}
export function salvarSessao(token, usuario) {
  localStorage.setItem(KEY_TOKEN, token);
  localStorage.setItem(KEY_USER, JSON.stringify(usuario));
}
export function sair() {
  localStorage.removeItem(KEY_TOKEN);
  localStorage.removeItem(KEY_USER);
  location.href = '/login';
}

// ---- chamadas ----
function mensagemDeErro(j, fallback) {
  if (!j || !j.detail) return fallback;
  if (Array.isArray(j.detail)) return j.detail.map(d => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ');
  return j.detail;
}

async function chamar(path, opts = {}, semRedirecionar = false) {
  const headers = {...(opts.headers || {})};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let r;
  try { r = await fetch(path, {...opts, headers}); }
  catch { throw new Error('Não foi possível conectar à API.'); }
  if (r.status === 401 && !semRedirecionar) { sair(); throw new Error('Sessão expirada.'); }
  if (!r.ok) {
    let j = null;
    try { j = await r.json(); } catch {}
    const err = new Error(mensagemDeErro(j, r.statusText));
    err.status = r.status;
    throw err;
  }
  return r.status === 204 ? null : r.json();
}

export const api = (path, opts) => chamar(path, opts);
export const apiJson = (path, metodo, corpo) =>
  chamar(path, {method: metodo, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)});

export async function login(email, senha) {
  const corpo = new URLSearchParams({username: email, password: senha});
  const r = await chamar('/auth/login', {method: 'POST', body: corpo}, true);
  salvarSessao(r.access_token, r.usuario);
  return r.usuario;
}

// ---- utilitários de interface ----
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
export const fmt = n => Number(n).toLocaleString('pt-BR', {maximumFractionDigits: 3});
// o servidor devolve datetimes "naive" em UTC (sem sufixo)
export const dataHora = s => s ? new Date(s + 'Z').toLocaleString('pt-BR') : '—';
export const hora = s => s ? new Date(s + 'Z').toLocaleTimeString('pt-BR') : '—';

export function toast(msg, tipo = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + tipo;
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), 5000);
}

// substituto do confirm() nativo: devolve Promise<boolean>
export function confirmar(mensagem, textoOk = 'Confirmar') {
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.innerHTML = `<form method="dialog"><h2>Confirmação</h2><p></p>
      <div class="linha"><button value="ok" class="grow">${esc(textoOk)}</button><button value="x" class="sm">Cancelar</button></div></form>`;
    d.querySelector('p').textContent = mensagem;
    d.addEventListener('close', () => { resolve(d.returnValue === 'ok'); d.remove(); });
    document.body.append(d);
    d.showModal();
  });
}

// lê um <form> em objeto JSON (números, checkboxes; vazio vira null)
export function formJson(f) {
  const o = {};
  for (const el of f.elements) {
    if (!el.name) continue;
    if (el.type === 'checkbox') o[el.name] = el.checked;
    else if (el.value === '') o[el.name] = null;
    else o[el.name] = el.type === 'number' || (el.tagName === 'SELECT' && /^\d+$/.test(el.value)) ? Number(el.value) : el.value;
  }
  return o;
}

// desabilita o botão de envio enquanto a ação roda
export async function comBotao(botao, fn) {
  if (botao) botao.disabled = true;
  try { return await fn(); } finally { if (botao) botao.disabled = false; }
}

export async function carregarUnidades(select) {
  try {
    const us = await api('/unidades');
    select.innerHTML = us.map(u => `<option value="${esc(u.valor)}">${esc(u.valor)} — ${esc(u.descricao)}</option>`).join('');
  } catch { /* o formulário continua utilizável sem as descrições */ }
}

// ---- consulta, paginação e exportação (relatórios) ----

// monta "?a=1&b=2" ignorando valores vazios
export function qs(params = {}) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') u.set(k, v);
  const s = u.toString();
  return s ? '?' + s : '';
}

export const fmtData = s => s ? new Date(s.length === 10 ? s + 'T00:00:00' : s + 'Z').toLocaleDateString('pt-BR') : '—';

// baixa um CSV mandando o token (um <a href> simples não envia o Authorization)
export async function baixarCsv(path, params, nomeArquivo) {
  const r = await fetch(path + qs({...params, formato: 'csv'}), {headers: {Authorization: `Bearer ${getToken()}`}});
  if (r.status === 401) { sair(); return; }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement('a'), {href: url, download: nomeArquivo + '.csv'});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// paginador: desenha em `el` e chama onMudar(novaPagina)
export function paginador(el, {pagina, tamanho, total}, onMudar) {
  const paginas = Math.max(1, Math.ceil(total / tamanho));
  el.innerHTML = `<div class="pager"><span class="mut">${total} registro(s) — página ${pagina} de ${paginas}</span>
    <span class="linha"><button class="sm" data-p="${pagina - 1}" ${pagina <= 1 ? 'disabled' : ''}>‹ Anterior</button>
    <button class="sm" data-p="${pagina + 1}" ${pagina >= paginas ? 'disabled' : ''}>Próxima ›</button></span></div>`;
  el.querySelectorAll('button[data-p]').forEach(b => b.onclick = () => onMudar(Number(b.dataset.p)));
}

// formata célula de tabela genérica (relatórios) conforme o tipo da coluna
export function celula(valor, tipo) {
  if (valor === null || valor === undefined) return '—';
  if (tipo === 'percentual') return Number(valor).toLocaleString('pt-BR', {maximumFractionDigits: 1}) + '%';
  if (tipo === 'numero') return fmt(valor);
  if (tipo === 'inteiro') return Number(valor).toLocaleString('pt-BR');
  if (tipo === 'data') return fmtData(valor);
  return esc(valor);
}

// atraso para campos de busca (evita uma chamada por tecla)
export function atrasar(fn, ms = 300) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
