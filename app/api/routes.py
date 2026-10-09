from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Material, Obra, Requisicao, StatusObra, StatusRequisicao
from app.schemas import (
    MaterialCreate, MaterialOut,
    ObraCreate, ObraOut,
    AndamentoUpdate, RequisicaoCreate, RequisicaoOut,
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


async def _remover(db: AsyncSession, entidade, coluna_req, forcar: bool):
    """Remove material/obra. Com histórico de requisições só remove com forcar=true;
    requisições ainda na fila/processando sempre bloqueiam a remoção."""
    abertas = (await db.execute(
        select(func.count()).select_from(Requisicao).where(
            coluna_req == entidade.id,
            Requisicao.status.in_([StatusRequisicao.PENDENTE, StatusRequisicao.EM_PROCESSAMENTO]),
        )
    )).scalar_one()
    if abertas:
        raise HTTPException(409, f"Há {abertas} requisição(ões) pendente(s)/em processamento. Aguarde o processamento.")
    historico = (await db.execute(
        select(func.count()).select_from(Requisicao).where(coluna_req == entidade.id)
    )).scalar_one()
    if historico and not forcar:
        raise HTTPException(409, f"Há {historico} requisição(ões) no histórico vinculadas. Confirme para remover tudo junto.")
    if historico:
        await db.execute(delete(Requisicao).where(coluna_req == entidade.id))
    await db.delete(entidade)
    await db.commit()


@router.delete("/materiais/{material_id}", status_code=204)
async def remover_material(material_id: int, forcar: bool = False, db: AsyncSession = Depends(get_db)):
    material = await db.get(Material, material_id)
    if material is None:
        raise HTTPException(404, "Material não encontrado")
    await _remover(db, material, Requisicao.material_id, forcar)


# ---------- Obras ----------

@router.delete("/obras/{obra_id}", status_code=204)
async def remover_obra(obra_id: int, forcar: bool = False, db: AsyncSession = Depends(get_db)):
    obra = await db.get(Obra, obra_id)
    if obra is None:
        raise HTTPException(404, "Obra não encontrada")
    await _remover(db, obra, Requisicao.obra_id, forcar)


@router.patch("/obras/{obra_id}/andamento", response_model=ObraOut)
async def atualizar_andamento(obra_id: int, payload: AndamentoUpdate, db: AsyncSession = Depends(get_db)):
    """Atualiza progresso (0-100) e descrição. progresso=100 ou finalizada=true encerra a obra;
    diminuir o progresso de uma obra finalizada a reabre."""
    obra = await db.get(Obra, obra_id)
    if obra is None:
        raise HTTPException(404, "Obra não encontrada")
    finalizar = payload.finalizada or payload.progresso == 100
    obra.descricao_andamento = payload.descricao_andamento
    if finalizar:
        obra.progresso = 100
        obra.parada_por_falta_material = False
        if obra.status != StatusObra.FINALIZADA.value:
            obra.status = StatusObra.FINALIZADA.value
            obra.finalizada_em = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        obra.progresso = payload.progresso
        obra.status = StatusObra.EM_ANDAMENTO.value
        obra.finalizada_em = None
    await db.commit()
    await db.refresh(obra)
    return obra

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
    if obra.status == StatusObra.FINALIZADA.value:
        raise HTTPException(409, "Obra finalizada não aceita novas requisições")
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
