"""
Modelos do domínio.

Material     -> item do estoque central (ex: cabo 10mm², disjuntor, eletroduto)
Obra         -> obra em andamento (Recife, Jaboatão, Olinda...) com prazo/prioridade
Requisicao   -> pedido de uma equipe/obra por uma quantidade de um material
Usuario      -> quem acessa o sistema (gestor, almoxarife ou engenheiro de obra)
MovimentacaoEstoque -> livro-razão do estoque (entradas, saídas das requisições, ajustes)
LogAuditoria -> registro de ações relevantes (quem fez o quê e quando)
"""
import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, String, Integer, ForeignKey, DateTime, Enum, Index, Numeric, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class StatusRequisicao(str, enum.Enum):
    PENDENTE = "pendente"          # na fila, ainda não processada
    EM_PROCESSAMENTO = "em_processamento"
    APROVADA = "aprovada"          # atendida integralmente
    PARCIAL = "parcial"            # atendida parcialmente (déficit)
    NEGADA = "negada"              # sem estoque algum
    ERRO = "erro"


class Perfil(str, enum.Enum):
    GESTOR = "gestor"              # acesso total
    ALMOXARIFE = "almoxarife"      # materiais/estoque; vê todas as requisições
    ENGENHEIRO = "engenheiro"      # equipe de obra: só a própria obra


class TipoMovimentacao(str, enum.Enum):
    ENTRADA = "entrada"    # recebimento/compra: soma ao estoque
    SAIDA = "saida"        # débito feito pelo processamento de uma requisição
    AJUSTE = "ajuste"      # correção de inventário (quantidade COM SINAL: + ou -)
    DEVOLUCAO = "devolucao"  # sobra devolvida pela obra: soma ao estoque (RF15 / RN10)


# Quantidades: NUMERIC(14,3) exato (achado 8). Float acumulava erro (0,1 somado 10 mil vezes != 1000).
# 14 dígitos no total, 3 decimais: cabe até 99.999.999.999,999 e ainda é representável sem perda em JSON/double.
QTD = Numeric(14, 3)
ZERO = Decimal("0")


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)   # datetimes "naive" em UTC, como o resto do projeto


class StatusObra(str, enum.Enum):
    EM_ANDAMENTO = "em_andamento"
    FINALIZADA = "finalizada"


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(128))
    perfil: Mapped[Perfil] = mapped_column(
        Enum(Perfil, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e])
    )
    ativo: Mapped[bool] = mapped_column(default=True, server_default=true())
    # obra da equipe (usado pelo perfil engenheiro)
    obra_id: Mapped[int | None] = mapped_column(ForeignKey("obras.id"), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Material(Base):
    __tablename__ = "materiais"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    unidade: Mapped[str] = mapped_column(String(10))  # "m", "un", "cx"...
    quantidade_estoque: Mapped[Decimal] = mapped_column(QTD, default=ZERO)
    estoque_critico: Mapped[Decimal] = mapped_column(QTD, default=ZERO)  # abaixo disso = alerta

    # exclusão lógica (achado 11): desativar preserva histórico, requisições, movimentações e auditoria
    ativo: Mapped[bool] = mapped_column(default=True, server_default=true())
    desativado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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
    ativo: Mapped[bool] = mapped_column(default=True, server_default=true())
    desativado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    requisicoes: Mapped[list["Requisicao"]] = relationship(back_populates="obra")


class Requisicao(Base):
    __tablename__ = "requisicoes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materiais.id"), index=True)

    # agrupa os itens enviados juntos num mesmo pedido (None = requisição avulsa)
    pedido_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # quem fez o pedido (auditoria); None em registros anteriores à autenticação
    solicitante_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    quantidade_solicitada: Mapped[Decimal] = mapped_column(QTD)
    quantidade_atendida: Mapped[Decimal] = mapped_column(QTD, default=ZERO)

    status: Mapped[StatusRequisicao] = mapped_column(
        Enum(StatusRequisicao), default=StatusRequisicao.PENDENTE, index=True
    )

    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    processado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(20), nullable=True)  # p/ debug/demo

    # fila com vários processos (achado 2): quem reivindicou a requisição e quando (prazo/lease: ver fila_service)
    reivindicada_por: Mapped[str | None] = mapped_column(String(60), nullable=True)
    reivindicada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # reprocessamento de erros (achado 3): falhas já ocorridas, último erro e quando tentar de novo
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxima_tentativa_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    obra: Mapped["Obra"] = relationship(back_populates="requisicoes")
    material: Mapped["Material"] = relationship(back_populates="requisicoes")


class MovimentacaoEstoque(Base):
    """
    Livro-razão do estoque. Invariante: saldo inicial + entradas + ajustes + devoluções - saídas == estoque atual.
    `quantidade` é positiva em ENTRADA/SAIDA/DEVOLUCAO e tem sinal em AJUSTE; `saldo_apos` é o estoque logo depois.
    """
    __tablename__ = "movimentacoes_estoque"
    __table_args__ = (Index("ix_movimentacoes_material_criado", "material_id", "criado_em"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materiais.id"), index=True)
    tipo: Mapped[TipoMovimentacao] = mapped_column(
        Enum(TipoMovimentacao, native_enum=False, length=10, values_callable=lambda e: [m.value for m in e])
    )
    quantidade: Mapped[Decimal] = mapped_column(QTD)
    saldo_apos: Mapped[Decimal] = mapped_column(QTD)
    requisicao_id: Mapped[str | None] = mapped_column(ForeignKey("requisicoes.id"), nullable=True, index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    # obra de origem (usada nas devoluções; limite = o que foi atendido para aquela obra e material)
    obra_id: Mapped[int | None] = mapped_column(ForeignKey("obras.id"), nullable=True, index=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # preenchido pelo app no flush (já dentro da seção crítica, com o lock do material): a ordem de
    # criado_em/id das movimentações de um material é a ordem em que o lock foi obtido
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=_agora, index=True)


class LogAuditoria(Base):
    """Quem fez o quê. `detalhes` nunca contém senhas nem tokens."""
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True, index=True)
    acao: Mapped[str] = mapped_column(String(50), index=True)
    entidade: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entidade_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detalhes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=_agora, index=True)
