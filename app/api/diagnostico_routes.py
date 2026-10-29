"""Diagnóstico de déficit (somente gestor e almoxarife; é uma visão global do estoque)."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import exigir_perfis
from app.core.database import get_db
from app.models import Perfil, Usuario
from app.schemas import DiagnosticoOut, SimulacaoIn
from app.services.diagnostico_service import diagnosticar

router = APIRouter(prefix="/diagnostico", tags=["diagnostico"])
gestor_ou_almoxarife = exigir_perfis(Perfil.GESTOR, Perfil.ALMOXARIFE)


@router.get("/deficit", response_model=DiagnosticoOut)
async def diagnostico_deficit(
    material_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(gestor_ou_almoxarife),
):
    """Simula a distribuição do estoque atual entre a demanda aberta (pendente + em processamento),
    na mesma ordem de prioridade da fila. Não grava nada."""
    return await diagnosticar(db, material_id)


@router.post("/simular", response_model=DiagnosticoOut)
async def simular(
    payload: SimulacaoIn,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(gestor_ou_almoxarife),
):
    """"E se a obra X pedir N?": junta requisições hipotéticas à demanda aberta e simula. Não grava nada."""
    return await diagnosticar(db, payload.material_id, payload.requisicoes)
