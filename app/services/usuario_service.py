import logging

from sqlalchemy import func, select

from app.core import config
from app.core.database import AsyncSessionLocal
from app.core.security import hash_senha
from app.models import Perfil, Usuario

logger = logging.getLogger("usuario_service")


async def criar_admin_inicial():
    """Cria o gestor inicial se ainda não existir nenhum usuário (credenciais do .env)."""
    async with AsyncSessionLocal() as db:
        total = (await db.execute(select(func.count()).select_from(Usuario))).scalar_one()
        if total:
            return
        db.add(Usuario(
            nome=config.ADMIN_NOME,
            email=config.ADMIN_EMAIL.strip().lower(),
            senha_hash=await hash_senha(config.ADMIN_SENHA),
            perfil=Perfil.GESTOR,
            ativo=True,
        ))
        await db.commit()
    logger.warning(
        "Gestor inicial criado: %s. TROQUE A SENHA (defina ADMIN_SENHA no .env antes do "
        "primeiro start ou use a tela de Usuários).", config.ADMIN_EMAIL,
    )
    if config.SECRET_KEY == config.SECRET_KEY_PADRAO:
        logger.warning("SECRET_KEY padrão em uso: defina uma chave própria no .env fora do desenvolvimento.")
