"""Login, usuário logado e gestão de usuários (somente gestor)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import exigir_perfis, get_current_user
from app.core.database import get_db
from app.core.security import criar_token, hash_senha, verificar_senha
from app.models import Obra, Perfil, Usuario
from app.schemas import SenhaReset, TokenOut, UsuarioCreate, UsuarioOut, UsuarioUpdate
from app.services import auditoria_service

router = APIRouter()
somente_gestor = exigir_perfis(Perfil.GESTOR)


# ---------- Auth ----------

@router.post("/auth/login", response_model=TokenOut, tags=["auth"])
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """Login por formulário (campo `username` = e-mail), compatível com o botão Authorize do Swagger."""
    email = form.username.strip().lower()
    usuario = (await db.execute(select(Usuario).where(Usuario.email == email))).scalar_one_or_none()
    # sempre verifica o hash (mesmo sem usuário) para não vazar quais e-mails existem
    senha_ok = await verificar_senha(form.password, usuario.senha_hash if usuario else None)
    if usuario is None or not senha_ok:
        # nunca registra a senha tentada; só o e-mail informado
        auditoria_service.registrar(db, usuario.id if usuario else None, "login_falhou", "usuario",
                                    usuario.id if usuario else None, {"email": email})
        await db.commit()
        raise HTTPException(401, "E-mail ou senha incorretos", headers={"WWW-Authenticate": "Bearer"})
    if not usuario.ativo:
        auditoria_service.registrar(db, usuario.id, "login_negado_inativo", "usuario", usuario.id, {"email": email})
        await db.commit()
        raise HTTPException(403, "Usuário desativado. Procure um gestor.")
    auditoria_service.registrar(db, usuario.id, "login", "usuario", usuario.id, {"email": email})
    await db.commit()
    return TokenOut(access_token=criar_token(usuario.id), usuario=UsuarioOut.model_validate(usuario))


@router.get("/auth/me", response_model=UsuarioOut, tags=["auth"])
async def me(usuario: Usuario = Depends(get_current_user)):
    return usuario


# ---------- Usuários (gestor) ----------

async def _validar_obra(db: AsyncSession, perfil: Perfil, obra_id: int | None) -> int | None:
    """Engenheiro precisa de uma obra existente; os demais perfis não têm obra."""
    if perfil != Perfil.ENGENHEIRO:
        return None
    if obra_id is None:
        raise HTTPException(422, "Engenheiro precisa estar vinculado a uma obra")
    if await db.get(Obra, obra_id) is None:
        raise HTTPException(404, "Obra não encontrada")
    return obra_id


@router.post("/usuarios", response_model=UsuarioOut, status_code=201, tags=["usuarios"])
async def criar_usuario(
    payload: UsuarioCreate,
    db: AsyncSession = Depends(get_db),
    logado: Usuario = Depends(somente_gestor),
):
    if (await db.execute(select(Usuario).where(Usuario.email == payload.email))).first():
        raise HTTPException(409, "Já existe um usuário com esse e-mail")
    obra_id = await _validar_obra(db, payload.perfil, payload.obra_id)
    usuario = Usuario(
        nome=payload.nome, email=payload.email, senha_hash=await hash_senha(payload.senha),
        perfil=payload.perfil, ativo=True, obra_id=obra_id,
    )
    db.add(usuario)
    await db.flush()
    auditoria_service.registrar(db, logado.id, "usuario_criado", "usuario", usuario.id, {
        "email": usuario.email, "perfil": usuario.perfil.value, "obra_id": usuario.obra_id})
    await db.commit()
    await db.refresh(usuario)
    return usuario


@router.get("/usuarios", response_model=list[UsuarioOut], tags=["usuarios"])
async def listar_usuarios(db: AsyncSession = Depends(get_db), _: Usuario = Depends(somente_gestor)):
    return (await db.execute(select(Usuario).order_by(Usuario.nome))).scalars().all()


@router.patch("/usuarios/{usuario_id}", response_model=UsuarioOut, tags=["usuarios"])
async def editar_usuario(
    usuario_id: int,
    payload: UsuarioUpdate,
    db: AsyncSession = Depends(get_db),
    logado: Usuario = Depends(somente_gestor),
):
    usuario = await db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(404, "Usuário não encontrado")
    campos = payload.model_fields_set
    perfil = payload.perfil if "perfil" in campos and payload.perfil else usuario.perfil
    ativo = payload.ativo if "ativo" in campos and payload.ativo is not None else usuario.ativo

    if usuario.id == logado.id and (perfil != Perfil.GESTOR or not ativo):
        # evita o sistema ficar sem nenhum gestor ativo por engano
        raise HTTPException(409, "Você não pode remover seu próprio acesso de gestor")

    obra_id = payload.obra_id if "obra_id" in campos else usuario.obra_id
    antes = {"nome": usuario.nome, "perfil": usuario.perfil.value, "ativo": usuario.ativo, "obra_id": usuario.obra_id}
    usuario.obra_id = await _validar_obra(db, perfil, obra_id)
    usuario.perfil = perfil
    usuario.ativo = ativo
    if "nome" in campos and payload.nome:
        usuario.nome = payload.nome
    auditoria_service.registrar(db, logado.id, "usuario_editado", "usuario", usuario.id, {
        "antes": antes,
        "depois": {"nome": usuario.nome, "perfil": usuario.perfil.value, "ativo": usuario.ativo, "obra_id": usuario.obra_id}})
    await db.commit()
    await db.refresh(usuario)
    return usuario


@router.post("/usuarios/{usuario_id}/redefinir-senha", status_code=204, tags=["usuarios"])
async def redefinir_senha(
    usuario_id: int,
    payload: SenhaReset,
    db: AsyncSession = Depends(get_db),
    logado: Usuario = Depends(somente_gestor),
):
    usuario = await db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(404, "Usuário não encontrado")
    usuario.senha_hash = await hash_senha(payload.nova_senha)
    auditoria_service.registrar(db, logado.id, "senha_redefinida", "usuario", usuario.id, {"email": usuario.email})
    await db.commit()
