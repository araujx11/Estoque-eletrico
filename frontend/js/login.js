import {getToken, login, $} from './api.js';

if (getToken()) location.replace('/');

$('#fLogin').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target, erro = $('#erro'), botao = $('button', f);
  erro.hidden = true;
  botao.disabled = true;
  botao.textContent = 'Entrando…';
  try {
    await login(f.email.value, f.senha.value);
    location.href = '/';
  } catch (err) {
    erro.textContent = err.message;
    erro.hidden = false;
    f.senha.value = '';
    f.senha.focus();
  } finally {
    botao.disabled = false;
    botao.textContent = 'Entrar';
  }
});
