"""Dependências de autenticação e autorização por perfil."""
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decodificar_token
from app.models import Perfil, Usuario

# tokenUrl faz o botão "Authorize" do Swagger (/docs) chamar o login por formulário
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> Usuario:
    """401 se o token for inválido/expirado ou o usuário não existir/estiver inativo."""
    erro = HTTPException(
        status_code=401,
        detail="Sessão inválida ou expirada. Faça login novamente.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    usuario_id = decodificar_token(token)
    if usuario_id is None:
        raise erro
    usuario = await db.get(Usuario, usuario_id)
    if usuario is None or not usuario.ativo:
        raise erro
    return usuario


def exigir_perfis(*perfis: Perfil):
    """Dependência que devolve o usuário logado, ou 403 se o perfil não estiver na lista."""
    permitidos = set(perfis)

    async def dependencia(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if usuario.perfil not in permitidos:
            raise HTTPException(403, "Seu perfil não tem permissão para esta operação")
        return usuario

    return dependencia
