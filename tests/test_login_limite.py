"""Achado 12: limite de tentativas de login por e-mail e por IP, com mensagem em português e registro na auditoria."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import LogAuditoria, TentativaLogin
from tests.conftest import SENHAS


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_TENTATIVAS", "3")
    monkeypatch.setenv("LOGIN_MAX_TENTATIVAS_IP", "20")
    monkeypatch.setenv("LOGIN_BLOQUEIO_MIN", "15")


async def _entrar(client, email, senha):
    return await client.post("/auth/login", data={"username": email, "password": senha})


async def _falhar(client, email, n):
    for _ in range(n):
        assert (await _entrar(client, email, "senha-errada-xx")).status_code == 401


async def _contar(SessionTeste, acao):
    async with SessionTeste() as db:
        return (await db.execute(select(func.count()).select_from(LogAuditoria).where(LogAuditoria.acao == acao))).scalar_one()


async def test_bloqueia_depois_de_n_falhas_com_mensagem_em_portugues(client, contas):
    await _falhar(client, "eng@teste.com", 3)
    r = await _entrar(client, "eng@teste.com", "senha-errada-xx")
    assert r.status_code == 429
    assert r.json()["detail"] == "Muitas tentativas. Tente novamente em 15 minutos."
    assert r.headers["retry-after"].isdigit() and 0 < int(r.headers["retry-after"]) <= 15 * 60


async def test_durante_o_bloqueio_nem_a_senha_certa_entra(client, contas):
    await _falhar(client, "eng@teste.com", 3)
    r = await _entrar(client, "eng@teste.com", SENHAS["eng"])
    assert r.status_code == 429 and "access_token" not in r.text


async def test_bloqueio_e_registrado_uma_unica_vez_e_nao_cresce_sob_ataque(client, contas, SessionTeste):
    await _falhar(client, "eng@teste.com", 3)
    for _ in range(10):                                                       # tentativas durante o bloqueio
        assert (await _entrar(client, "eng@teste.com", "x")).status_code == 429
    assert await _contar(SessionTeste, "login_bloqueado") == 1                # auditoria: só ao cruzar o limite
    assert await _contar(SessionTeste, "login_falhou") == 3                   # as recusadas não geram linhas (achado 12)
    async with SessionTeste() as db:
        assert (await db.execute(select(func.count()).select_from(TentativaLogin))).scalar_one() == 3
        log = (await db.execute(select(LogAuditoria).where(LogAuditoria.acao == "login_bloqueado"))).scalar_one()
    assert log.detalhes["email"] == "eng@teste.com" and log.detalhes["motivo"] == "email" and "senha" not in str(log.detalhes).lower()


async def test_outro_email_nao_e_bloqueado_pelo_limite_de_email(client, contas):
    await _falhar(client, "eng@teste.com", 3)
    assert (await _entrar(client, "almox@teste.com", SENHAS["almox"])).status_code == 200


async def test_email_inexistente_tambem_bloqueia_com_a_mesma_mensagem(client, contas):
    await _falhar(client, "nao-existe@teste.com", 3)
    r = await _entrar(client, "nao-existe@teste.com", "qualquer-coisa")
    assert r.status_code == 429 and r.json()["detail"].startswith("Muitas tentativas")   # não revela quem existe


async def test_bloqueio_por_ip_com_emails_diferentes(client, contas, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_TENTATIVAS", "50")
    monkeypatch.setenv("LOGIN_MAX_TENTATIVAS_IP", "4")
    for i in range(4):                                                        # 4 e-mails diferentes, mesmo IP
        assert (await _entrar(client, f"x{i}@teste.com", "errada-xxxx")).status_code == 401
    r = await _entrar(client, "almox@teste.com", SENHAS["almox"])             # até um usuário válido é barrado
    assert r.status_code == 429 and "Muitas tentativas" in r.json()["detail"]


async def test_login_com_sucesso_zera_as_falhas_do_email(client, contas):
    await _falhar(client, "eng@teste.com", 2)
    assert (await _entrar(client, "eng@teste.com", SENHAS["eng"])).status_code == 200
    await _falhar(client, "eng@teste.com", 2)                                 # recomeça do zero: ainda não bloqueia
    assert (await _entrar(client, "eng@teste.com", SENHAS["eng"])).status_code == 200


async def test_bloqueio_expira_com_o_tempo(client, contas, SessionTeste):
    await _falhar(client, "eng@teste.com", 3)
    assert (await _entrar(client, "eng@teste.com", SENHAS["eng"])).status_code == 429
    async with SessionTeste() as db:                                          # envelhece as falhas além da janela
        await db.execute(update(TentativaLogin).values(criado_em=func.now() - timedelta(minutes=16)))
        await db.commit()
    assert (await _entrar(client, "eng@teste.com", SENHAS["eng"])).status_code == 200


async def test_minutos_restantes_diminuem(client, contas, SessionTeste):
    await _falhar(client, "eng@teste.com", 3)
    async with SessionTeste() as db:
        await db.execute(update(TentativaLogin).values(criado_em=func.now() - timedelta(minutes=12, seconds=30)))
        await db.commit()
    r = await _entrar(client, "eng@teste.com", "x")
    assert r.status_code == 429 and r.json()["detail"] == "Muitas tentativas. Tente novamente em 3 minutos."


async def test_um_minuto_fica_no_singular(client, contas, SessionTeste):
    await _falhar(client, "eng@teste.com", 3)
    async with SessionTeste() as db:
        await db.execute(update(TentativaLogin).values(criado_em=func.now() - timedelta(minutes=14, seconds=10)))
        await db.commit()
    r = await _entrar(client, "eng@teste.com", "x")
    assert r.json()["detail"] == "Muitas tentativas. Tente novamente em 1 minuto."


async def test_falhas_antigas_sao_apagadas_aos_poucos(client, contas, SessionTeste):
    await _falhar(client, "eng@teste.com", 1)
    async with SessionTeste() as db:
        await db.execute(update(TentativaLogin).values(criado_em=func.now() - timedelta(hours=2)))
        await db.commit()
    await _falhar(client, "almox@teste.com", 1)                               # uma nova falha limpa as antigas
    async with SessionTeste() as db:
        emails = (await db.execute(select(TentativaLogin.email))).scalars().all()
    assert emails == ["almox@teste.com"]


async def test_a_senha_tentada_nunca_e_gravada(client, contas, SessionTeste):
    await _entrar(client, "eng@teste.com", "MINHA-SENHA-SECRETA-123")
    async with SessionTeste() as db:
        linhas = (await db.execute(select(TentativaLogin.email, TentativaLogin.ip))).all()
        logs = (await db.execute(select(LogAuditoria.detalhes))).scalars().all()
    assert "SECRETA" not in str(linhas) + str(logs)
