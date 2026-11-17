import enum
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.models import Perfil, StatusRequisicao


# Quantidade exata (NUMERIC 14,3 no banco). No JSON sai como NÚMERO (não texto): com no máximo 14 dígitos
# significativos o double não perde nada, e o frontend continua recebendo números.
Qtd = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
# entrada: até 3 casas e 11 inteiros (cabe em NUMERIC(14,3)); acima disso a API recusa em vez de arredondar
QtdIn = dict(max_digits=14, decimal_places=3)


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
    quantidade_estoque: Decimal = Field(ge=0, **QtdIn)
    estoque_critico: Decimal = Field(default=Decimal(0), ge=0, **QtdIn)


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nome: str
    unidade: str
    quantidade_estoque: Qtd
    estoque_critico: Qtd
    ativo: bool = True
    desativado_em: datetime | None = None


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
    status: str
    progresso: int
    descricao_andamento: str | None
    finalizada_em: datetime | None
    ativo: bool = True
    desativado_em: datetime | None = None


class AtivoUpdate(BaseModel):
    """Reativa (ou desativa) um material/obra, preservando todo o histórico."""
    ativo: bool


class AndamentoUpdate(BaseModel):
    progresso: int = Field(ge=0, le=100)
    descricao_andamento: str | None = Field(default=None, max_length=1000)
    finalizada: bool = False


class RequisicaoCreate(BaseModel):
    obra_id: int
    material_id: int
    quantidade_solicitada: Decimal = Field(gt=0, **QtdIn)


class ItemPedido(BaseModel):
    material_id: int
    quantidade_solicitada: Decimal = Field(gt=0, **QtdIn)


class PedidoCreate(BaseModel):
    obra_id: int
    itens: list[ItemPedido] = Field(min_length=1, max_length=50)

    @field_validator("itens")
    @classmethod
    def _sem_materiais_repetidos(cls, v):
        ids = [i.material_id for i in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Material repetido no pedido; some as quantidades num único item")
        return v


class RequisicaoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    obra_id: int
    material_id: int
    pedido_id: str | None = None
    quantidade_solicitada: Qtd
    quantidade_atendida: Qtd
    status: StatusRequisicao
    criado_em: datetime
    processado_em: datetime | None
    worker_id: str | None
    solicitante_id: int | None = None
    tentativas: int = 0
    ultimo_erro: str | None = None


class ResumoOut(BaseModel):
    total_requisicoes: int
    por_status: dict[str, int]
    materiais_criticos: int
    fila_pendente: int


class DashboardOut(BaseModel):
    total_materiais: int
    materiais_criticos: int
    lista_criticos: list[MaterialOut]
    total_obras: int
    total_requisicoes: int
    por_status: dict[str, int]
    fila_pendente: int


# ---------- Autenticação / usuários ----------

def _validar_senha(v: str) -> str:
    # bcrypt só considera os 72 primeiros bytes; recusamos em vez de truncar em silêncio
    if len(v.encode("utf-8")) > 72:
        raise ValueError("A senha pode ter no máximo 72 bytes")
    return v


class UsuarioCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
    senha: str = Field(min_length=8)
    perfil: Perfil
    obra_id: int | None = None

    @field_validator("email")
    @classmethod
    def _email_minusculo(cls, v: str):
        return v.strip().lower()

    _senha = field_validator("senha")(_validar_senha)


class UsuarioUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    perfil: Perfil | None = None
    ativo: bool | None = None
    obra_id: int | None = None  # enviar null explícito desvincula a obra


class SenhaReset(BaseModel):
    nova_senha: str = Field(min_length=8)

    _senha = field_validator("nova_senha")(_validar_senha)


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nome: str
    email: str
    perfil: Perfil
    ativo: bool
    obra_id: int | None
    criado_em: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioOut


# ---------- Movimentações de estoque ----------

class EntradaCreate(BaseModel):
    material_id: int
    quantidade: Decimal = Field(gt=0, **QtdIn)
    observacao: str | None = Field(default=None, max_length=500)


class AjusteCreate(BaseModel):
    material_id: int
    quantidade: Decimal = Field(**QtdIn)  # delta com sinal: positivo soma, negativo subtrai
    observacao: str = Field(min_length=3, max_length=500)  # obrigatória em ajustes

    @field_validator("quantidade")
    @classmethod
    def _nao_zero(cls, v: Decimal):
        if v == 0:
            raise ValueError("O ajuste não pode ser zero")
        return v

    @field_validator("observacao")
    @classmethod
    def _observacao_nao_vazia(cls, v: str):
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Informe o motivo do ajuste (mín. 3 caracteres)")
        return v


class DevolucaoCreate(BaseModel):
    material_id: int
    quantidade: Decimal = Field(gt=0, **QtdIn)
    observacao: str = Field(min_length=3, max_length=500)        # obrigatória: de onde veio / por que voltou
    obra_id: int | None = None                                    # obra de origem (limita a devolução ao que ela recebeu)
    requisicao_id: str | None = Field(default=None, max_length=36)

    @field_validator("observacao")
    @classmethod
    def _observacao_nao_vazia(cls, v: str):
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Informe o motivo da devolução (mín. 3 caracteres)")
        return v


class MovimentacaoOut(BaseModel):
    id: int
    material_id: int
    material_nome: str
    unidade: str
    tipo: str
    quantidade: Qtd
    saldo_apos: Qtd
    requisicao_id: str | None
    usuario_id: int | None
    usuario_nome: str | None
    obra_id: int | None = None
    obra_nome: str | None = None
    observacao: str | None
    criado_em: datetime


class MovimentacoesPagina(BaseModel):
    total: int
    pagina: int
    tamanho: int
    itens: list[MovimentacaoOut]


# ---------- Auditoria ----------

class AuditoriaOut(BaseModel):
    id: int
    usuario_id: int | None
    usuario_nome: str | None
    acao: str
    entidade: str | None
    entidade_id: str | None
    detalhes: dict | None
    criado_em: datetime


class AuditoriaPagina(BaseModel):
    total: int
    pagina: int
    tamanho: int
    itens: list[AuditoriaOut]


# ---------- Diagnóstico de déficit ----------

class LinhaSimulacao(BaseModel):
    ordem: int
    requisicao_id: str | None          # None = requisição hipotética
    hipotetica: bool = False
    obra_id: int
    obra_nome: str
    obra_parada: bool
    nivel_prioridade: int
    prazo_entrega: datetime | None
    criado_em: datetime
    quantidade_pedida: Qtd
    quantidade_atendida: Qtd
    quantidade_pendente: Qtd
    status: str                        # completa | parcial | sem_atendimento
    icone: str
    rotulo: str


class MaterialDiagnostico(BaseModel):
    material_id: int
    nome: str
    unidade: str
    estoque_atual: Qtd
    estoque_critico: Qtd
    demanda_total: Qtd
    deficit: Qtd
    estoque_restante: Qtd
    semaforo: str                      # verde | amarelo | vermelho
    icone: str
    mensagem: str
    requisicoes: list[LinhaSimulacao]


class DeficitHistorico(BaseModel):
    material_id: int
    nome: str
    unidade: str
    requisicoes_parciais: int
    requisicoes_negadas: int
    deficit_acumulado: Qtd


class RequisicaoComErro(BaseModel):
    """Requisição que falhou no processamento (ERRO). `definitivo`: esgotou as tentativas e não será mais reprocessada."""
    requisicao_id: str
    obra_id: int
    obra_nome: str
    material_id: int
    material_nome: str
    unidade: str
    quantidade_solicitada: Qtd
    tentativas: int
    max_tentativas: int
    definitivo: bool
    ultimo_erro: str | None
    proxima_tentativa_em: datetime | None


class DiagnosticoOut(BaseModel):
    gerado_em: datetime
    simulacao_hipotetica: bool
    materiais: list[MaterialDiagnostico]
    deficit_historico: list[DeficitHistorico]
    erros: list[RequisicaoComErro] = []


class ItemHipotetico(BaseModel):
    obra_id: int
    material_id: int
    quantidade: Decimal = Field(gt=0, **QtdIn)


class SimulacaoIn(BaseModel):
    requisicoes: list[ItemHipotetico] = Field(min_length=1, max_length=50)
    material_id: int | None = None     # opcional: limita a resposta a um material


# ---------- Relatórios (formato genérico de tabela, usado também pelo CSV) ----------

class ColunaOut(BaseModel):
    chave: str
    titulo: str
    tipo: str = "texto"                # texto | inteiro | numero | percentual | data | datahora


class TabelaOut(BaseModel):
    titulo: str
    colunas: list[ColunaOut]
    total: int
    pagina: int
    tamanho: int
    itens: list[dict]
    resumo: dict | None = None
