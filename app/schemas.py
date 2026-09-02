from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.models import StatusRequisicao


class MaterialCreate(BaseModel):
    nome: str
    unidade: str
    quantidade_estoque: float
    estoque_critico: float = 0


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nome: str
    unidade: str
    quantidade_estoque: float
    estoque_critico: float


class ObraCreate(BaseModel):
    nome: str
    cidade: str
    prazo_entrega: datetime | None = None
    parada_por_falta_material: bool = False
    nivel_prioridade: int = 5


class ObraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nome: str
    cidade: str
    prazo_entrega: datetime | None
    parada_por_falta_material: bool
    nivel_prioridade: int


class RequisicaoCreate(BaseModel):
    obra_id: int
    material_id: int
    quantidade_solicitada: float


class RequisicaoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    obra_id: int
    material_id: int
    quantidade_solicitada: float
    quantidade_atendida: float
    status: StatusRequisicao
    criado_em: datetime
    processado_em: datetime | None
    worker_id: str | None
