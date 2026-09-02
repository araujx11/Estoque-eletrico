from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Material, Obra, Requisicao, StatusRequisicao
from app.schemas import (
    MaterialCreate, MaterialOut,
    ObraCreate, ObraOut,
    RequisicaoCreate, RequisicaoOut,
)
from app.services.fila_service import fila_global

router = APIRouter()


# ---------- Materiais ----------

@router.post("/materiais", response_model=MaterialOut)
async def criar_material(payload: MaterialCreate, db: AsyncSession = Depends(get_db)):
    material = Material(**payload.model_dump())
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return material


@router.get("/materiais", response_model=list[MaterialOut])
async def listar_materiais(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Material))
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
    result = await db.execute(select(Obra))
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
