import enum
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import StatusRequisicao


class Unidade(str, enum.Enum):
    """Unidades de medida predefinidas (valor gravado = sigla)."""
    METRO = "m"
    UNIDADE = "un"
    CAIXA = "cx"
    ROLO = "rolo"
    BARRA = "barra"
    PECA = "pç"
    PAR = "par"
    KG = "kg"
    LITRO = "L"


UNIDADES_DESCRICAO = {
    Unidade.METRO: "Metro (cabos, fios)",
    Unidade.UNIDADE: "Unidade (disjuntores, tomadas)",
    Unidade.CAIXA: "Caixa",
    Unidade.ROLO: "Rolo (fita, cabo)",
    Unidade.BARRA: "Barra (eletroduto, perfilado)",
    Unidade.PECA: "Peça",
    Unidade.PAR: "Par",
    Unidade.KG: "Quilograma",
    Unidade.LITRO: "Litro",
}


class MaterialCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    unidade: Unidade = Unidade.UNIDADE
    quantidade_estoque: float = Field(ge=0)
    estoque_critico: float = Field(default=0, ge=0)


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nome: str
    unidade: str
    quantidade_estoque: float
    estoque_critico: float


class ObraCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    cidade: str = Field(min_length=1, max_length=80)
    prazo_entrega: datetime | None = None
    parada_por_falta_material: bool = False
    nivel_prioridade: int = Field(default=5, ge=1, le=5)

    @field_validator("prazo_entrega")
    @classmethod
    def _prazo_naive_utc(cls, v: datetime | None):
        # a coluna é DateTime sem fuso; asyncpg rejeita datetime com tzinfo
        if v is not None and v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v


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
    quantidade_solicitada: float = Field(gt=0)


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


class ResumoOut(BaseModel):
    total_requisicoes: int
    por_status: dict[str, int]
    materiais_criticos: int
    fila_pendente: int
