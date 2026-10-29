"""Hash de senha (bcrypt) e tokens JWT (PyJWT)."""
import asyncio
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core import config

# hash "de mentira" usado quando o e-mail não existe, para o tempo de resposta
# do login não revelar se o usuário está cadastrado
_HASH_FALSO = bcrypt.hashpw(b"senha-falsa", bcrypt.gensalt()).decode()


def _hash(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode()


def _verificar(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode("utf-8")[:72], senha_hash.encode())
    except ValueError:
        return False


# bcrypt é CPU-bound (~100-300ms): roda em thread para não travar o event loop,
# onde os workers de estoque também executam.
async def hash_senha(senha: str) -> str:
    return await asyncio.to_thread(_hash, senha)


async def verificar_senha(senha: str, senha_hash: str | None) -> bool:
    ok = await asyncio.to_thread(_verificar, senha, senha_hash or _HASH_FALSO)
    return ok and senha_hash is not None


def criar_token(usuario_id: int) -> str:
    agora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario_id),
        "iat": agora,
        "exp": agora + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.ALGORITHM)


def decodificar_token(token: str) -> int | None:
    """Retorna o id do usuário ou None se o token for inválido/expirado."""
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
