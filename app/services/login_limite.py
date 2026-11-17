"""
Limite de tentativas de login (achado 12): depois de N falhas, bloqueia por e-mail e por IP durante alguns minutos.

* Contagem no banco (tabela `tentativas_login`), então vale com vários processos da API.
* Falhas dentro da janela (`LOGIN_BLOQUEIO_MIN`) contam; quando o número chega ao limite, novas tentativas são recusadas (429) SEM
  verificar a senha (nem a correta passa) e SEM gravar mais nada: o bloqueio não se estende e a tabela não cresce sob ataque.
* O bloqueio vale para qualquer e-mail digitado, exista ou não, com a mesma mensagem (não revela quais e-mails existem).
* Limite por IP maior que o por e-mail (`LOGIN_MAX_TENTATIVAS_IP`), porque vários usuários podem compartilhar um IP (escritório/NAT).
  Atrás de um proxy reverso o IP visto é o do proxy: configure o uvicorn com `--proxy-headers` e `--forwarded-allow-ips`.
* Entrar com sucesso zera as falhas daquele e-mail.
"""
import math
import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TentativaLogin
from app.services import auditoria_service


def _num(nome: str, padrao: float) -> float:
    try:
        return float(os.getenv(nome, padrao))
    except ValueError:
        return padrao


def max_email() -> int:
    return max(1, int(_num("LOGIN_MAX_TENTATIVAS", 5)))


def max_ip() -> int:
    return max(1, int(_num("LOGIN_MAX_TENTATIVAS_IP", 20)))


def janela() -> timedelta:
    return timedelta(minutes=max(0.01, _num("LOGIN_BLOQUEIO_MIN", 15)))


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mensagem(minutos: int) -> str:
    return f"Muitas tentativas. Tente novamente em {minutos} {'minuto' if minutos == 1 else 'minutos'}."


async def _espera_s(db: AsyncSession, coluna, valor: str, limite: int) -> float:
    """Segundos até a chave deixar de estar bloqueada (0 = livre)."""
    agora = _agora()
    instantes = (await db.execute(
        select(TentativaLogin.criado_em).where(coluna == valor, TentativaLogin.criado_em > agora - janela())
        .order_by(TentativaLogin.criado_em)
    )).scalars().all()
    if len(instantes) < limite:
        return 0.0
    # o bloqueio cai quando a falha que "passa do limite" sai da janela
    liberacao = instantes[len(instantes) - limite] + janela()
    return max(0.0, (liberacao - agora).total_seconds())


async def verificar(db: AsyncSession, email: str, ip: str) -> None:
    """Levanta 429 (com `Retry-After`) se o e-mail ou o IP estiver bloqueado."""
    espera = max(await _espera_s(db, TentativaLogin.email, email, max_email()),
                 await _espera_s(db, TentativaLogin.ip, ip, max_ip()))
    if espera > 0:
        raise HTTPException(429, _mensagem(max(1, math.ceil(espera / 60))), headers={"Retry-After": str(math.ceil(espera))})


async def registrar_falha(db: AsyncSession, email: str, ip: str) -> None:
    """Conta a falha (o commit é de quem chama). Se ela leva o e-mail ou o IP ao limite, registra o bloqueio na auditoria."""
    agora = _agora()
    await db.execute(delete(TentativaLogin).where(TentativaLogin.criado_em <= agora - janela()))     # limpeza oportunista
    db.add(TentativaLogin(email=email, ip=ip, criado_em=agora))
    await db.flush()
    for coluna, valor, limite, tipo in ((TentativaLogin.email, email, max_email(), "email"), (TentativaLogin.ip, ip, max_ip(), "ip")):
        n = (await db.execute(select(func.count()).select_from(TentativaLogin).where(coluna == valor))).scalar_one()
        if n == limite:                                   # exatamente ao cruzar: uma linha de auditoria por bloqueio
            auditoria_service.registrar(db, None, "login_bloqueado", "usuario", None, {
                "motivo": tipo, "email": email, "ip": ip, "tentativas": n, "bloqueio_minutos": janela().total_seconds() / 60})


async def zerar(db: AsyncSession, email: str) -> None:
    await db.execute(delete(TentativaLogin).where(TentativaLogin.email == email))
