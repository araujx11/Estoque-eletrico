"""
Modelos do domínio.

Material     -> item do estoque central (ex: cabo 10mm², disjuntor, eletroduto)
Obra         -> obra em andamento (Recife, Jaboatão, Olinda...) com prazo/prioridade
Requisicao   -> pedido de uma equipe/obra por uma quantidade de um material
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, Enum, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class StatusRequisicao(str, enum.Enum):
    PENDENTE = "pendente"          # na fila, ainda não processada
    EM_PROCESSAMENTO = "em_processamento"
    APROVADA = "aprovada"          # atendida integralmente
    PARCIAL = "parcial"            # atendida parcialmente (déficit)
    NEGADA = "negada"              # sem estoque algum
    ERRO = "erro"


class StatusObra(str, enum.Enum):
    EM_ANDAMENTO = "em_andamento"
    FINALIZADA = "finalizada"


class Material(Base):
    __tablename__ = "materiais"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    unidade: Mapped[str] = mapped_column(String(10))  # "m", "un", "cx"...
    quantidade_estoque: Mapped[float] = mapped_column(Float, default=0)
    estoque_critico: Mapped[float] = mapped_column(Float, default=0)  # abaixo disso = alerta

    requisicoes: Mapped[list["Requisicao"]] = relationship(back_populates="material")


class Obra(Base):
    __tablename__ = "obras"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    cidade: Mapped[str] = mapped_column(String(80))
    prazo_entrega: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    parada_por_falta_material: Mapped[bool] = mapped_column(default=False)
    # menor número = maior prioridade (1 = mais urgente)
    nivel_prioridade: Mapped[int] = mapped_column(Integer, default=5)
    # andamento: progresso 0-100; 100 <=> finalizada
    status: Mapped[str] = mapped_column(String(20), default=StatusObra.EM_ANDAMENTO.value, server_default=StatusObra.EM_ANDAMENTO.value)
    progresso: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    descricao_andamento: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalizada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    requisicoes: Mapped[list["Requisicao"]] = relationship(back_populates="obra")


class Requisicao(Base):
    __tablename__ = "requisicoes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materiais.id"))

    quantidade_solicitada: Mapped[float] = mapped_column(Float)
    quantidade_atendida: Mapped[float] = mapped_column(Float, default=0)

    status: Mapped[StatusRequisicao] = mapped_column(
        Enum(StatusRequisicao), default=StatusRequisicao.PENDENTE
    )

    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(20), nullable=True)  # p/ debug/demo

    obra: Mapped["Obra"] = relationship(back_populates="requisicoes")
    material: Mapped["Material"] = relationship(back_populates="requisicoes")
