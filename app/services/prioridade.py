"""
Regra de prioridade das requisições, compartilhada pela fila e pelo diagnóstico de déficit.

Menor chave = atendida primeiro. Cascata de critérios:
  1. Obra parada por falta de material
  2. Nível de prioridade da obra (1 = mais urgente)
  3. Prazo de entrega mais próximo (sem prazo = por último)
  4. Requisição mais antiga (FIFO como desempate final)
"""
from datetime import datetime, timezone


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def chave_prioridade(
    parada_por_falta_material: bool,
    nivel_prioridade: int,
    prazo_entrega: datetime | None,
    criado_em: datetime,
) -> tuple:
    criado_em = _naive_utc(criado_em)
    if prazo_entrega is not None:
        prazo_entrega = _naive_utc(prazo_entrega)
    return (
        0 if not parada_por_falta_material else -1,  # obra parada vem primeiro
        nivel_prioridade,
        prazo_entrega or datetime.max,
        criado_em,
    )
