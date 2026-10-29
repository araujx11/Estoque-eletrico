"""
Diagnóstico de déficit: SIMULA a distribuição do estoque atual entre a demanda aberta.

Somente leitura: nada é gravado e nenhum lock é tomado. A ordem de atendimento usa a MESMA
chave de prioridade da fila (app/services/prioridade.py) e a alocação repete a regra do
estoque_service (atende o que houver; zerou, o resto fica sem atendimento).

Semáforo por material (restante = estoque - o que a demanda consumiria):
  🟢 verde    estoque cobre toda a demanda e o restante fica ACIMA do crítico
  🟡 amarelo  cobre a demanda, mas o restante fica no crítico ou abaixo (mesma regra "<=" do alerta de estoque)
  🔴 vermelho estoque insuficiente (déficit > 0)
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Material, Obra, Requisicao, StatusRequisicao
from app.schemas import (
    DeficitHistorico, DiagnosticoOut, ItemHipotetico, LinhaSimulacao, MaterialDiagnostico,
)
from app.services.prioridade import chave_prioridade

ABERTAS = (StatusRequisicao.PENDENTE, StatusRequisicao.EM_PROCESSAMENTO)
EPS = 1e-9

ICONE_SEMAFORO = {"verde": "🟢", "amarelo": "🟡", "vermelho": "🔴"}
ROTULO_LINHA = {
    "completa": ("🟢", "Atendida por completo"),
    "parcial": ("🟡", "Atendida parcialmente"),
    "sem_atendimento": ("🔴", "Sem atendimento"),
}
ORDEM_SEMAFORO = {"vermelho": 0, "amarelo": 1, "verde": 2}


def _fmt(n: float) -> str:
    return f"{n:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def classificar(estoque: float, critico: float, demanda: float) -> tuple[str, float, float]:
    """(semáforo, déficit, restante) para um material."""
    deficit = max(0.0, demanda - estoque)
    restante = estoque - min(demanda, estoque)
    if deficit > EPS:
        return "vermelho", deficit, restante
    if restante <= critico + EPS:
        return "amarelo", deficit, restante
    return "verde", deficit, restante


def _mensagem(nome: str, un: str, estoque: float, critico: float, demanda: float,
              deficit: float, restante: float, semaforo: str) -> str:
    if semaforo == "vermelho":
        return (f"{nome} insuficiente. Estoque: {_fmt(estoque)} {un}. "
                f"Demanda: {_fmt(demanda)} {un}. Déficit: {_fmt(deficit)} {un}")
    if semaforo == "amarelo":
        if demanda <= EPS:
            return (f"{nome}: sem demanda aberta, mas o estoque ({_fmt(estoque)} {un}) está no nível "
                    f"crítico ({_fmt(critico)} {un}) ou abaixo.")
        return (f"{nome}: o estoque cobre a demanda ({_fmt(demanda)} {un}), mas o que sobra "
                f"({_fmt(restante)} {un}) ficaria no crítico ({_fmt(critico)} {un}) ou abaixo.")
    if demanda <= EPS:
        return f"{nome}: sem demanda aberta e estoque acima do crítico."
    return f"{nome}: o estoque cobre a demanda ({_fmt(demanda)} {un}) e o restante fica acima do crítico."


async def diagnosticar(
    db: AsyncSession,
    material_id: int | None = None,
    hipoteticas: list[ItemHipotetico] | None = None,
) -> DiagnosticoOut:
    hipoteticas = hipoteticas or []
    # as várias consultas abaixo (materiais, demanda, linhas, histórico) precisam enxergar o MESMO instante do
    # banco; em READ COMMITTED um pedido processado no meio delas deixaria a simulação incoerente
    # (a autenticação já abriu uma transação READ COMMITTED nesta sessão; encerra-a para poder trocar o isolamento)
    await db.rollback()
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})

    consulta_mat = select(Material).order_by(Material.nome)
    if material_id is not None:
        consulta_mat = consulta_mat.where(Material.id == material_id)
    materiais = (await db.execute(consulta_mat)).scalars().all()
    if material_id is not None and not materiais:
        raise HTTPException(404, "Material não encontrado")

    # demanda total por material: agregado em SQL
    q_dem = (select(Requisicao.material_id, func.sum(Requisicao.quantidade_solicitada))
             .where(Requisicao.status.in_(ABERTAS)).group_by(Requisicao.material_id))
    if material_id is not None:
        q_dem = q_dem.where(Requisicao.material_id == material_id)
    demanda = {m: float(q) for m, q in (await db.execute(q_dem)).all()}

    # linhas da simulação: só as requisições abertas (conjunto limitado), com os dados da obra
    q_lin = (select(Requisicao, Obra).join(Obra, Obra.id == Requisicao.obra_id)
             .where(Requisicao.status.in_(ABERTAS)).order_by(Requisicao.criado_em, Requisicao.id))
    if material_id is not None:
        q_lin = q_lin.where(Requisicao.material_id == material_id)
    entradas: dict[int, list[dict]] = {}
    for req, obra in (await db.execute(q_lin)).all():
        entradas.setdefault(req.material_id, []).append(dict(
            requisicao_id=req.id, hipotetica=False, obra=obra, pedida=req.quantidade_solicitada, criado_em=req.criado_em))

    if hipoteticas:
        obras = {o.id: o for o in (await db.execute(
            select(Obra).where(Obra.id.in_({h.obra_id for h in hipoteticas})))).scalars().all()}
        existentes = {i for (i,) in (await db.execute(select(Material.id))).all()}
        agora = datetime.now(timezone.utc).replace(tzinfo=None)
        for i, h in enumerate(hipoteticas):
            if h.obra_id not in obras:
                raise HTTPException(404, f"Obra {h.obra_id} não encontrada")
            if h.material_id not in existentes:
                raise HTTPException(404, f"Material {h.material_id} não encontrado")
            demanda[h.material_id] = demanda.get(h.material_id, 0.0) + h.quantidade
            # chegam depois de tudo que já está na fila (desempate pelo mais antigo)
            entradas.setdefault(h.material_id, []).append(dict(
                requisicao_id=None, hipotetica=True, obra=obras[h.obra_id], pedida=h.quantidade,
                criado_em=agora + timedelta(microseconds=i)))

    resultado: list[MaterialDiagnostico] = []
    for m in materiais:
        fila = sorted(entradas.get(m.id, []), key=lambda e: chave_prioridade(
            e["obra"].parada_por_falta_material, e["obra"].nivel_prioridade, e["obra"].prazo_entrega, e["criado_em"]))
        disponivel = m.quantidade_estoque
        linhas = []
        for ordem, e in enumerate(fila, start=1):
            pedida = e["pedida"]
            if disponivel <= 0:
                atendida = 0.0
            elif disponivel >= pedida:
                atendida = pedida
            else:
                atendida = disponivel
            disponivel -= atendida
            status = "completa" if atendida >= pedida - EPS else ("sem_atendimento" if atendida <= EPS else "parcial")
            icone, rotulo = ROTULO_LINHA[status]
            o = e["obra"]
            linhas.append(LinhaSimulacao(
                ordem=ordem, requisicao_id=e["requisicao_id"], hipotetica=e["hipotetica"], obra_id=o.id,
                obra_nome=o.nome, obra_parada=o.parada_por_falta_material, nivel_prioridade=o.nivel_prioridade,
                prazo_entrega=o.prazo_entrega, criado_em=e["criado_em"], quantidade_pedida=pedida,
                quantidade_atendida=atendida, quantidade_pendente=pedida - atendida,
                status=status, icone=icone, rotulo=rotulo))
        dem = demanda.get(m.id, 0.0)
        semaforo, deficit, restante = classificar(m.quantidade_estoque, m.estoque_critico, dem)
        resultado.append(MaterialDiagnostico(
            material_id=m.id, nome=m.nome, unidade=m.unidade, estoque_atual=m.quantidade_estoque,
            estoque_critico=m.estoque_critico, demanda_total=dem, deficit=deficit, estoque_restante=restante,
            semaforo=semaforo, icone=ICONE_SEMAFORO[semaforo],
            mensagem=_mensagem(m.nome, m.unidade, m.quantidade_estoque, m.estoque_critico, dem, deficit, restante, semaforo),
            requisicoes=linhas))
    resultado.sort(key=lambda r: (ORDEM_SEMAFORO[r.semaforo], r.nome))

    # déficit histórico (já aconteceu): parciais e negadas, agregado em SQL
    q_hist = (
        select(
            Material.id, Material.nome, Material.unidade,
            func.count().filter(Requisicao.status == StatusRequisicao.PARCIAL),
            func.count().filter(Requisicao.status == StatusRequisicao.NEGADA),
            func.sum(Requisicao.quantidade_solicitada - Requisicao.quantidade_atendida),
        )
        .join(Requisicao, Requisicao.material_id == Material.id)
        .where(Requisicao.status.in_((StatusRequisicao.PARCIAL, StatusRequisicao.NEGADA)))
        .group_by(Material.id, Material.nome, Material.unidade)
        .order_by(func.sum(Requisicao.quantidade_solicitada - Requisicao.quantidade_atendida).desc())
    )
    if material_id is not None:
        q_hist = q_hist.where(Material.id == material_id)
    historico = [DeficitHistorico(
        material_id=i, nome=n, unidade=u, requisicoes_parciais=p, requisicoes_negadas=ng, deficit_acumulado=float(d or 0))
        for i, n, u, p, ng, d in (await db.execute(q_hist)).all()]

    return DiagnosticoOut(
        gerado_em=datetime.now(timezone.utc).replace(tzinfo=None),
        simulacao_hipotetica=bool(hipoteticas), materiais=resultado, deficit_historico=historico)
