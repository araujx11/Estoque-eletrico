from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Material, Obra, Requisicao, StatusRequisicao
from app.schemas import (
    MaterialCreate, MaterialOut,
    ObraCreate, ObraOut,
    RequisicaoCreate, RequisicaoOut,
    ResumoOut, Unidade, UNIDADES_DESCRICAO,
)
from app.services.fila_service import fila_global

router = APIRouter()


# ---------- Unidades / Resumo ----------

@router.get("/unidades")
async def listar_unidades():
    return [{"valor": u.value, "descricao": UNIDADES_DESCRICAO[u]} for u in Unidade]


@router.get("/resumo", response_model=ResumoOut)
async def resumo(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Requisicao.status, func.count()).group_by(Requisicao.status)
    )).all()
    por_status = {s.value: 0 for s in StatusRequisicao}
    for status, n in rows:
        por_status[status.value] = n
    criticos = (await db.execute(
        select(func.count()).select_from(Material)
        .where(Material.quantidade_estoque <= Material.estoque_critico)
    )).scalar_one()
    return ResumoOut(
        total_requisicoes=sum(por_status.values()),
        por_status=por_status,
        materiais_criticos=criticos,
        fila_pendente=fila_global.tamanho(),
    )


# ---------- Materiais ----------

@router.post("/materiais", response_model=MaterialOut)
async def criar_material(payload: MaterialCreate, db: AsyncSession = Depends(get_db)):
    dados = payload.model_dump()
    dados["unidade"] = payload.unidade.value
    if (await db.execute(select(Material).where(Material.nome == payload.nome))).first():
        raise HTTPException(409, "Já existe um material com esse nome")
    material = Material(**dados)
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return material


@router.get("/materiais", response_model=list[MaterialOut])
async def listar_materiais(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Material).order_by(Material.nome))
    return result.scalars().all()


# ---------- Obras ----------

@router.post("/obras", response_model=ObraOut)
async def criar_obra(payload: ObraCreate, db: AsyncSession = Depends(get_db)):
    obra = Obra(**payload.model_dump())
    db.add(obra)
    await db.commit()
    await db.refresh(obra)
    return obra


@router.get("/obras", response_model=list[ObraOut])
async def listar_obras(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Obra).order_by(Obra.id))
    return result.scalars().all()


# ---------- Requisições ----------

@router.post("/requisicoes", response_model=RequisicaoOut, status_code=202)
async def criar_requisicao(payload: RequisicaoCreate, db: AsyncSession = Depends(get_db)):
    """
    Cria a requisição com status PENDENTE e a coloca na fila.
    A resposta 202 (Accepted) reflete que o processamento é assíncrono:
    o cliente deve consultar GET /requisicoes/{id} para saber o resultado.
    """
    obra = await db.get(Obra, payload.obra_id)
    if obra is None:
        raise HTTPException(404, "Obra não encontrada")
    material = await db.get(Material, payload.material_id)
    if material is None:
        raise HTTPException(404, "Material não encontrado")

    requisicao = Requisicao(
        obra_id=payload.obra_id,
        material_id=payload.material_id,
        quantidade_solicitada=payload.quantidade_solicitada,
    )
    db.add(requisicao)
    await db.commit()
    await db.refresh(requisicao)

    await fila_global.adicionar(
        requisicao_id=requisicao.id,
        parada_por_falta_material=obra.parada_por_falta_material,
        nivel_prioridade=obra.nivel_prioridade,
        prazo_entrega=obra.prazo_entrega,
        criado_em=requisicao.criado_em or datetime.now(timezone.utc),
    )
    return requisicao


@router.get("/requisicoes/{requisicao_id}", response_model=RequisicaoOut)
async def status_requisicao(requisicao_id: str, db: AsyncSession = Depends(get_db)):
    requisicao = await db.get(Requisicao, requisicao_id)
    if requisicao is None:
        raise HTTPException(404, "Requisição não encontrada")
    return requisicao


@router.get("/requisicoes", response_model=list[RequisicaoOut])
async def listar_requisicoes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Requisicao).order_by(Requisicao.criado_em.desc()))
    return result.scalars().all()
