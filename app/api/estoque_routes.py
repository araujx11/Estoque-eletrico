"""Movimentações de estoque (livro-razão) e auditoria."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import exigir_perfis
from app.core.database import get_db
from app.models import (
    LogAuditoria, Material, MovimentacaoEstoque, Obra, Perfil, Requisicao, StatusRequisicao, TipoMovimentacao, Usuario,
)
from app.schemas import (
    AjusteCreate, AuditoriaOut, AuditoriaPagina, DevolucaoCreate, EntradaCreate, MaterialOut, MovimentacaoOut, MovimentacoesPagina,
)
from app.services import auditoria_service

router = APIRouter()
somente_gestor = exigir_perfis(Perfil.GESTOR)
gestor_ou_almoxarife = exigir_perfis(Perfil.GESTOR, Perfil.ALMOXARIFE)


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
        select(MovimentacaoEstoque, Material.nome, Material.unidade, Usuario.nome, Obra.nome)
        .join(Material, Material.id == MovimentacaoEstoque.material_id)
        .outerjoin(Usuario, Usuario.id == MovimentacaoEstoque.usuario_id)
        .outerjoin(Obra, Obra.id == MovimentacaoEstoque.obra_id)
        .where(*filtros)
        .order_by(MovimentacaoEstoque.criado_em.desc(), MovimentacaoEstoque.id.desc())
        .limit(tamanho).offset((pagina - 1) * tamanho)
    )).all()
    itens = [MovimentacaoOut(
        id=m.id, material_id=m.material_id, material_nome=mat, unidade=un, tipo=m.tipo.value,
        quantidade=m.quantidade, saldo_apos=m.saldo_apos, requisicao_id=m.requisicao_id,
        usuario_id=m.usuario_id, usuario_nome=usr, obra_id=m.obra_id, obra_nome=obra, observacao=m.observacao,
        criado_em=m.criado_em,
    ) for m, mat, un, usr, obra in linhas]
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
    if not material.ativo:
        raise HTTPException(409, "Material desativado não aceita novas entradas. Reative-o primeiro.")
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
    if novo_saldo < 0:
        raise HTTPException(
            409, f"O ajuste deixaria o estoque negativo (atual: {material.quantidade_estoque.normalize():f} {material.unidade}).")
    material.quantidade_estoque = novo_saldo
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


def _fmt(q) -> str:
    return f"{q.normalize():f}".replace(".", ",")


@router.post("/movimentacoes/devolucao", response_model=MaterialOut, status_code=201, tags=["estoque"])
async def registrar_devolucao(
    payload: DevolucaoCreate, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(gestor_ou_almoxarife),
):
    """Devolução de material por uma obra (RF15/RN10): AUMENTA o saldo. Não pode devolver mais do que foi atendido
    para a obra e o material, descontadas as devoluções já feitas. Tudo na mesma transação, com o lock do material
    (as devoluções do mesmo material se serializam, então o limite não é furado por duas ao mesmo tempo)."""
    material = await _material_travado(db, payload.material_id)
    obra_id = payload.obra_id
    if payload.requisicao_id is not None:
        req = await db.get(Requisicao, payload.requisicao_id)
        if req is None:
            raise HTTPException(404, "Requisição não encontrada")
        if req.material_id != material.id:
            raise HTTPException(422, "A requisição informada é de outro material")
        if obra_id is not None and obra_id != req.obra_id:
            raise HTTPException(422, "A requisição informada é de outra obra")
        obra_id = req.obra_id
    if obra_id is not None and await db.get(Obra, obra_id) is None:
        raise HTTPException(404, "Obra não encontrada")

    # sem obra informada o limite é o total atendido do material (todas as obras)
    filtro_req = [Requisicao.material_id == material.id,
                  Requisicao.status.in_((StatusRequisicao.APROVADA, StatusRequisicao.PARCIAL))]
    filtro_dev = [MovimentacaoEstoque.material_id == material.id, MovimentacaoEstoque.tipo == TipoMovimentacao.DEVOLUCAO]
    if obra_id is not None:
        filtro_req.append(Requisicao.obra_id == obra_id)
        filtro_dev.append(MovimentacaoEstoque.obra_id == obra_id)
    atendido = (await db.execute(select(func.coalesce(func.sum(Requisicao.quantidade_atendida), 0)).where(*filtro_req))).scalar_one()
    devolvido = (await db.execute(select(func.coalesce(func.sum(MovimentacaoEstoque.quantidade), 0)).where(*filtro_dev))).scalar_one()
    devolvivel = atendido - devolvido
    if payload.quantidade > devolvivel:
        destino = "para esta obra e este material" if obra_id is not None else "deste material"
        raise HTTPException(
            409, f"Devolução acima do limite: só é possível devolver {_fmt(devolvivel)} {material.unidade} {destino} "
                 f"(atendido {_fmt(atendido)}, já devolvido {_fmt(devolvido)}).")

    material.quantidade_estoque += payload.quantidade
    db.add(MovimentacaoEstoque(
        material_id=material.id, tipo=TipoMovimentacao.DEVOLUCAO, quantidade=payload.quantidade,
        saldo_apos=material.quantidade_estoque, usuario_id=usuario.id, obra_id=obra_id,
        requisicao_id=payload.requisicao_id, observacao=payload.observacao,
    ))
    auditoria_service.registrar(db, usuario.id, "devolucao_estoque", "material", material.id, {
        "material": material.nome, "quantidade": payload.quantidade, "saldo_apos": material.quantidade_estoque,
        "obra_id": obra_id, "requisicao_id": payload.requisicao_id, "observacao": payload.observacao,
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
