"""Movimentações de estoque (livro-razão) e auditoria."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import exigir_perfis
from app.core.database import get_db
from app.models import LogAuditoria, Material, MovimentacaoEstoque, Perfil, TipoMovimentacao, Usuario
from app.schemas import (
    AjusteCreate, AuditoriaOut, AuditoriaPagina, EntradaCreate, MaterialOut, MovimentacaoOut, MovimentacoesPagina,
)
from app.services import auditoria_service

router = APIRouter()
somente_gestor = exigir_perfis(Perfil.GESTOR)
gestor_ou_almoxarife = exigir_perfis(Perfil.GESTOR, Perfil.ALMOXARIFE)

EPS = 1e-9


def intervalo(de: date | None, ate: date | None):
    """Datas inclusivas -> [inicio, fim) em datetime naive."""
    ini = datetime.combine(de, time.min) if de else None
    fim = datetime.combine(ate + timedelta(days=1), time.min) if ate else None
    return ini, fim


# ---------- Movimentações ----------

@router.get("/movimentacoes", response_model=MovimentacoesPagina, tags=["estoque"])
async def listar_movimentacoes(
    material_id: int | None = None,
    tipo: TipoMovimentacao | None = None,
    de: date | None = None,
    ate: date | None = None,
    busca: str | None = Query(default=None, max_length=100, description="texto no nome do material ou na observação"),
    pagina: int = Query(default=1, ge=1),
    tamanho: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(gestor_ou_almoxarife),
):
    filtros = []
    if material_id:
        filtros.append(MovimentacaoEstoque.material_id == material_id)
    if tipo:
        filtros.append(MovimentacaoEstoque.tipo == tipo)
    ini, fim = intervalo(de, ate)
    if ini:
        filtros.append(MovimentacaoEstoque.criado_em >= ini)
    if fim:
        filtros.append(MovimentacaoEstoque.criado_em < fim)
    if busca:
        like = f"%{busca.strip()}%"
        filtros.append(Material.nome.ilike(like) | MovimentacaoEstoque.observacao.ilike(like))

    base = select(MovimentacaoEstoque).join(Material, Material.id == MovimentacaoEstoque.material_id).where(*filtros)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    linhas = (await db.execute(
        select(MovimentacaoEstoque, Material.nome, Material.unidade, Usuario.nome)
        .join(Material, Material.id == MovimentacaoEstoque.material_id)
        .outerjoin(Usuario, Usuario.id == MovimentacaoEstoque.usuario_id)
        .where(*filtros)
        .order_by(MovimentacaoEstoque.criado_em.desc(), MovimentacaoEstoque.id.desc())
        .limit(tamanho).offset((pagina - 1) * tamanho)
    )).all()
    itens = [MovimentacaoOut(
        id=m.id, material_id=m.material_id, material_nome=mat, unidade=un, tipo=m.tipo.value,
        quantidade=m.quantidade, saldo_apos=m.saldo_apos, requisicao_id=m.requisicao_id,
        usuario_id=m.usuario_id, usuario_nome=usr, observacao=m.observacao, criado_em=m.criado_em,
    ) for m, mat, un, usr in linhas]
    return MovimentacoesPagina(total=total, pagina=pagina, tamanho=tamanho, itens=itens)


async def _material_travado(db: AsyncSession, material_id: int) -> Material:
    """Mesmo lock pessimista dos workers: entradas/ajustes não se perdem nem furam um débito em curso."""
    material = (await db.execute(
        select(Material).where(Material.id == material_id).with_for_update()
    )).scalar_one_or_none()
    if material is None:
        raise HTTPException(404, "Material não encontrado")
    return material


@router.post("/movimentacoes/entrada", response_model=MaterialOut, status_code=201, tags=["estoque"])
async def registrar_entrada(
    payload: EntradaCreate, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(gestor_ou_almoxarife),
):
    """Recebimento/compra: soma ao estoque e registra a movimentação (mesma transação)."""
    material = await _material_travado(db, payload.material_id)
    material.quantidade_estoque += payload.quantidade
    db.add(MovimentacaoEstoque(
        material_id=material.id, tipo=TipoMovimentacao.ENTRADA, quantidade=payload.quantidade,
        saldo_apos=material.quantidade_estoque, usuario_id=usuario.id, observacao=payload.observacao,
    ))
    auditoria_service.registrar(db, usuario.id, "entrada_estoque", "material", material.id, {
        "material": material.nome, "quantidade": payload.quantidade, "saldo_apos": material.quantidade_estoque,
        "observacao": payload.observacao,
    })
    await db.commit()
    await db.refresh(material)
    return material


@router.post("/movimentacoes/ajuste", response_model=MaterialOut, status_code=201, tags=["estoque"])
async def registrar_ajuste(
    payload: AjusteCreate, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(gestor_ou_almoxarife),
):
    """Ajuste de inventário (quantidade com sinal). Nunca deixa o estoque negativo."""
    material = await _material_travado(db, payload.material_id)
    novo_saldo = material.quantidade_estoque + payload.quantidade
    if novo_saldo < -EPS:
        raise HTTPException(
            409, f"O ajuste deixaria o estoque negativo (atual: {material.quantidade_estoque:g} {material.unidade}).")
    material.quantidade_estoque = max(0.0, novo_saldo)
    db.add(MovimentacaoEstoque(
        material_id=material.id, tipo=TipoMovimentacao.AJUSTE, quantidade=payload.quantidade,
        saldo_apos=material.quantidade_estoque, usuario_id=usuario.id, observacao=payload.observacao,
    ))
    auditoria_service.registrar(db, usuario.id, "ajuste_estoque", "material", material.id, {
        "material": material.nome, "quantidade": payload.quantidade, "saldo_apos": material.quantidade_estoque,
        "observacao": payload.observacao,
    })
    await db.commit()
    await db.refresh(material)
    return material


# ---------- Auditoria (somente gestor) ----------

@router.get("/auditoria", response_model=AuditoriaPagina, tags=["auditoria"])
async def listar_auditoria(
    usuario_id: int | None = None,
    acao: str | None = Query(default=None, max_length=50),
    entidade: str | None = Query(default=None, max_length=50),
    de: date | None = None,
    ate: date | None = None,
    pagina: int = Query(default=1, ge=1),
    tamanho: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(somente_gestor),
):
    filtros = []
    if usuario_id:
        filtros.append(LogAuditoria.usuario_id == usuario_id)
    if acao:
        filtros.append(LogAuditoria.acao == acao)
    if entidade:
        filtros.append(LogAuditoria.entidade == entidade)
    ini, fim = intervalo(de, ate)
    if ini:
        filtros.append(LogAuditoria.criado_em >= ini)
    if fim:
        filtros.append(LogAuditoria.criado_em < fim)
    total = (await db.execute(select(func.count()).select_from(LogAuditoria).where(*filtros))).scalar_one()
    linhas = (await db.execute(
        select(LogAuditoria, Usuario.nome)
        .outerjoin(Usuario, Usuario.id == LogAuditoria.usuario_id)
        .where(*filtros)
        .order_by(LogAuditoria.criado_em.desc(), LogAuditoria.id.desc())
        .limit(tamanho).offset((pagina - 1) * tamanho)
    )).all()
    itens = [AuditoriaOut(
        id=l.id, usuario_id=l.usuario_id, usuario_nome=nome, acao=l.acao, entidade=l.entidade,
        entidade_id=l.entidade_id, detalhes=l.detalhes, criado_em=l.criado_em,
    ) for l, nome in linhas]
    return AuditoriaPagina(total=total, pagina=pagina, tamanho=tamanho, itens=itens)


@router.get("/auditoria/acoes", response_model=list[str], tags=["auditoria"])
async def listar_acoes_auditadas(db: AsyncSession = Depends(get_db), _: Usuario = Depends(somente_gestor)):
    """Ações já registradas (para o filtro da tela)."""
    return list((await db.execute(select(LogAuditoria.acao).distinct().order_by(LogAuditoria.acao))).scalars().all())
