"""
Relatórios, exportação CSV e dashboard completo.

Todos os totais são calculados no banco (GROUP BY / SUM / COUNT / percentile_cont); o Python só
formata. O período filtra pela data de CRIAÇÃO do pedido (criado_em). Engenheiro enxerga apenas a
própria obra; o relatório de desempenho do processamento é restrito a gestor/almoxarife.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.estoque_routes import intervalo
from app.core.auth import exigir_perfis, get_current_user
from app.core.database import get_db
from app.models import Material, Obra, Perfil, Requisicao, StatusRequisicao as S, Usuario
from app.schemas import ColunaOut, TabelaOut
from app.services.csv_export import resposta_csv
from app.services.fila_service import fila_global

router = APIRouter(tags=["relatorios"])
gestor_ou_almoxarife = exigir_perfis(Perfil.GESTOR, Perfil.ALMOXARIFE)

ABERTAS = (S.PENDENTE, S.EM_PROCESSAMENTO)
RESOLVIDAS = (S.APROVADA, S.PARCIAL, S.NEGADA)
LIMITE_CSV = 50_000


# ---------------------------------------------------------------- utilidades

def _escopo_obra(usuario: Usuario, obra_id: int | None) -> int | None:
    """Engenheiro: sempre a própria obra (403 se pedir outra). Demais perfis: o filtro informado."""
    if usuario.perfil != Perfil.ENGENHEIRO:
        return obra_id
    if obra_id is not None and obra_id != usuario.obra_id:
        raise HTTPException(403, "Você só pode consultar a sua própria obra")
    return usuario.obra_id if usuario.obra_id is not None else -1   # sem obra: nada a mostrar


def _filtrar(q, de, ate, obra_id=None, material_id=None, status=None):
    ini, fim = intervalo(de, ate)
    if ini:
        q = q.where(Requisicao.criado_em >= ini)
    if fim:
        q = q.where(Requisicao.criado_em < fim)
    if obra_id is not None:
        q = q.where(Requisicao.obra_id == obra_id)
    if material_id is not None:
        q = q.where(Requisicao.material_id == material_id)
    if status is not None:
        q = q.where(Requisicao.status == status)
    return q


def _valor(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v
    return v


def _linha(row) -> dict:
    return {k: _valor(v) for k, v in row._mapping.items()}


def _ordem(mapa: dict, ordenar: str, direcao: str, desempate=None):
    if ordenar not in mapa:
        raise HTTPException(422, f"Ordenação inválida: {ordenar}. Use: {', '.join(mapa)}")
    expr = mapa[ordenar]
    exprs = [expr.desc() if direcao == "desc" else expr.asc()]
    if desempate is not None:
        exprs.append(desempate)
    return exprs


async def _tabela(db, titulo, colunas, consulta, ordem, pagina, tamanho, formato, arquivo, resumo=None):
    total = (await db.execute(select(func.count()).select_from(consulta.subquery()))).scalar_one()
    consulta = consulta.order_by(*ordem)
    cols = [c.model_dump() for c in colunas]
    if formato == "csv":
        linhas = [_linha(r) for r in (await db.execute(consulta.limit(LIMITE_CSV))).all()]
        return resposta_csv(arquivo, cols, linhas)
    linhas = [_linha(r) for r in (await db.execute(consulta.limit(tamanho).offset((pagina - 1) * tamanho))).all()]
    return TabelaOut(titulo=titulo, colunas=colunas, total=total, pagina=pagina, tamanho=tamanho,
                     itens=linhas, resumo=resumo)


Formato = Literal["json", "csv"]
Direcao = Literal["asc", "desc"]


# ---------------------------------------------------------------- 1) consumo

@router.get("/relatorios/consumo", response_model=TabelaOut)
async def relatorio_consumo(
    agrupar: Literal["obra_material", "material", "obra"] = "obra_material",
    de: date | None = None, ate: date | None = None,
    obra_id: int | None = None, material_id: int | None = None, status: S | None = None,
    ordenar: str = "quantidade", direcao: Direcao = "desc",
    pagina: int = Query(default=1, ge=1), tamanho: int = Query(default=20, ge=1, le=200),
    formato: Formato = "json",
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user),
):
    """Consumo (quantidade atendida) por obra e material no período. Padrão: pedidos aprovados ou parciais."""
    obra_id = _escopo_obra(usuario, obra_id)
    qtd = func.coalesce(func.sum(Requisicao.quantidade_atendida), 0.0)
    sol = func.coalesce(func.sum(Requisicao.quantidade_solicitada), 0.0)
    n = func.count()
    j = (select().select_from(Requisicao)
         .join(Obra, Obra.id == Requisicao.obra_id).join(Material, Material.id == Requisicao.material_id))

    if agrupar == "obra_material":
        q = j.add_columns(Obra.nome.label("obra"), Material.nome.label("material"), Material.unidade.label("unidade"),
                          qtd.label("quantidade_atendida"), sol.label("quantidade_solicitada"), n.label("requisicoes")
                          ).group_by(Obra.id, Obra.nome, Material.id, Material.nome, Material.unidade)
        colunas = [ColunaOut(chave="obra", titulo="Obra"), ColunaOut(chave="material", titulo="Material"),
                   ColunaOut(chave="unidade", titulo="Unidade"),
                   ColunaOut(chave="quantidade_atendida", titulo="Quantidade atendida", tipo="numero"),
                   ColunaOut(chave="quantidade_solicitada", titulo="Quantidade solicitada", tipo="numero"),
                   ColunaOut(chave="requisicoes", titulo="Requisições", tipo="inteiro")]
        mapa = {"quantidade": qtd, "requisicoes": n, "obra": Obra.nome, "material": Material.nome}
    elif agrupar == "material":
        q = j.add_columns(Material.nome.label("material"), Material.unidade.label("unidade"),
                          qtd.label("quantidade_atendida"), sol.label("quantidade_solicitada"), n.label("requisicoes")
                          ).group_by(Material.id, Material.nome, Material.unidade)
        colunas = [ColunaOut(chave="material", titulo="Material"), ColunaOut(chave="unidade", titulo="Unidade"),
                   ColunaOut(chave="quantidade_atendida", titulo="Quantidade atendida", tipo="numero"),
                   ColunaOut(chave="quantidade_solicitada", titulo="Quantidade solicitada", tipo="numero"),
                   ColunaOut(chave="requisicoes", titulo="Requisições", tipo="inteiro")]
        mapa = {"quantidade": qtd, "requisicoes": n, "material": Material.nome}
    else:  # obra: unidades diferentes não se somam, então conta itens atendidos
        q = j.add_columns(Obra.nome.label("obra"), n.label("itens_atendidos"),
                          func.count(func.distinct(Requisicao.material_id)).label("materiais_distintos")
                          ).group_by(Obra.id, Obra.nome)
        colunas = [ColunaOut(chave="obra", titulo="Obra"),
                   ColunaOut(chave="itens_atendidos", titulo="Itens atendidos", tipo="inteiro"),
                   ColunaOut(chave="materiais_distintos", titulo="Materiais distintos", tipo="inteiro")]
        mapa = {"quantidade": n, "requisicoes": n, "obra": Obra.nome}

    q = _filtrar(q, de, ate, obra_id, material_id)
    q = q.where(Requisicao.status == status) if status else q.where(Requisicao.status.in_((S.APROVADA, S.PARCIAL)))
    return await _tabela(db, "Consumo no período", colunas, q, _ordem(mapa, ordenar, direcao, list(mapa.values())[-1]),
                         pagina, tamanho, formato, "consumo")


# ---------------------------------------------------------------- 2) requisições por período

def _serie(de, ate, obra_id, material_id, agrupar: str):
    periodo = func.date_trunc("day" if agrupar == "dia" else "week", Requisicao.criado_em)
    cont = lambda st: func.count().filter(Requisicao.status == st)  # noqa: E731
    q = select(periodo.label("periodo"), func.count().label("total"),
               *[cont(st).label(st.value) for st in S]).group_by(periodo)
    return _filtrar(q, de, ate, obra_id, material_id), periodo


COLUNAS_SERIE = [ColunaOut(chave="periodo", titulo="Período", tipo="data"), ColunaOut(chave="total", titulo="Total", tipo="inteiro"),
                 *[ColunaOut(chave=st.value, titulo=t, tipo="inteiro") for st, t in (
                     (S.PENDENTE, "Pendentes"), (S.EM_PROCESSAMENTO, "Em processamento"), (S.APROVADA, "Aprovadas"),
                     (S.PARCIAL, "Parciais"), (S.NEGADA, "Negadas"), (S.ERRO, "Erros"))]]


@router.get("/relatorios/requisicoes-por-periodo", response_model=TabelaOut)
async def relatorio_por_periodo(
    agrupar: Literal["dia", "semana"] = "dia",
    de: date | None = None, ate: date | None = None,
    obra_id: int | None = None, material_id: int | None = None,
    direcao: Direcao = "asc",
    pagina: int = Query(default=1, ge=1), tamanho: int = Query(default=31, ge=1, le=200),
    formato: Formato = "json",
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user),
):
    """Série temporal de requisições por status (por dia ou por semana)."""
    obra_id = _escopo_obra(usuario, obra_id)
    q, periodo = _serie(de, ate, obra_id, material_id, agrupar)
    resp = await _tabela(db, "Requisições por período", COLUNAS_SERIE, q,
                         [periodo.desc() if direcao == "desc" else periodo.asc()], pagina, tamanho, formato, "requisicoes-por-periodo")
    if isinstance(resp, TabelaOut):
        for it in resp.itens:
            it["periodo"] = it["periodo"].date().isoformat()
    return resp


# ---------------------------------------------------------------- 3) estoque crítico

@router.get("/relatorios/estoque-critico", response_model=TabelaOut)
async def relatorio_estoque_critico(
    busca: str | None = Query(default=None, max_length=100),
    ordenar: str = "percentual", direcao: Direcao = "asc",
    pagina: int = Query(default=1, ge=1), tamanho: int = Query(default=20, ge=1, le=200),
    formato: Formato = "json",
    db: AsyncSession = Depends(get_db), _: Usuario = Depends(get_current_user),
):
    """Materiais no nível crítico ou abaixo, com quanto falta para atingir o crítico."""
    pct = case((Material.estoque_critico > 0, Material.quantidade_estoque / Material.estoque_critico * 100), else_=None)
    falta = func.greatest(Material.estoque_critico - Material.quantidade_estoque, 0.0)
    situacao = case((Material.quantidade_estoque <= 0, "Zerado"),
                    (Material.quantidade_estoque < Material.estoque_critico, "Abaixo do crítico"),
                    else_="No limite do crítico")
    q = select(Material.nome.label("material"), Material.unidade.label("unidade"),
               Material.quantidade_estoque.label("estoque"), Material.estoque_critico.label("critico"),
               falta.label("falta_para_critico"), pct.label("percentual_do_critico"), situacao.label("situacao")
               ).where(Material.quantidade_estoque <= Material.estoque_critico)
    if busca:
        q = q.where(Material.nome.ilike(f"%{busca.strip()}%"))
    colunas = [ColunaOut(chave="material", titulo="Material"), ColunaOut(chave="unidade", titulo="Unidade"),
               ColunaOut(chave="estoque", titulo="Estoque atual", tipo="numero"),
               ColunaOut(chave="critico", titulo="Estoque crítico", tipo="numero"),
               ColunaOut(chave="falta_para_critico", titulo="Falta para o crítico", tipo="numero"),
               ColunaOut(chave="percentual_do_critico", titulo="% do crítico", tipo="percentual"),
               ColunaOut(chave="situacao", titulo="Situação")]
    mapa = {"percentual": func.coalesce(pct, -1), "falta": falta, "nome": Material.nome}
    return await _tabela(db, "Materiais em estoque crítico", colunas, q, _ordem(mapa, ordenar, direcao, Material.nome),
                         pagina, tamanho, formato, "estoque-critico")


# ---------------------------------------------------------------- 4) taxa de atendimento

@router.get("/relatorios/taxa-atendimento", response_model=TabelaOut)
async def relatorio_taxa_atendimento(
    de: date | None = None, ate: date | None = None,
    obra_id: int | None = None, material_id: int | None = None,
    ordenar: str = "deficit", direcao: Direcao = "desc",
    pagina: int = Query(default=1, ge=1), tamanho: int = Query(default=20, ge=1, le=200),
    formato: Formato = "json",
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user),
):
    """% de pedidos aprovados/parciais/negados e déficit acumulado por material (só pedidos já resolvidos)."""
    obra_id = _escopo_obra(usuario, obra_id)
    cont = lambda st: func.count().filter(Requisicao.status == st)  # noqa: E731
    sol = func.sum(Requisicao.quantidade_solicitada)
    ate_q = func.sum(Requisicao.quantidade_atendida)
    deficit = sol - ate_q
    taxa_qtd = case((sol > 0, ate_q / sol * 100), else_=None)
    taxa_aprov = cont(S.APROVADA) * 100.0 / func.count()

    q = (select(Material.nome.label("material"), Material.unidade.label("unidade"), func.count().label("requisicoes"),
                cont(S.APROVADA).label("aprovadas"), cont(S.PARCIAL).label("parciais"), cont(S.NEGADA).label("negadas"),
                sol.label("solicitado"), ate_q.label("atendido"), deficit.label("deficit_acumulado"),
                taxa_aprov.label("pct_aprovadas"), taxa_qtd.label("taxa_atendimento_qtd"))
         .select_from(Requisicao).join(Material, Material.id == Requisicao.material_id)
         .where(Requisicao.status.in_(RESOLVIDAS)).group_by(Material.id, Material.nome, Material.unidade))
    q = _filtrar(q, de, ate, obra_id, material_id)

    r = (select(func.count(), cont(S.APROVADA), cont(S.PARCIAL), cont(S.NEGADA),
                func.coalesce(func.sum(Requisicao.quantidade_solicitada), 0.0),
                func.coalesce(func.sum(Requisicao.quantidade_atendida), 0.0))
         .where(Requisicao.status.in_(RESOLVIDAS)))
    total, ap, pa, ne, s_, a_ = (await db.execute(_filtrar(r, de, ate, obra_id, material_id))).one()
    pc = lambda x: round(x * 100.0 / total, 1) if total else None  # noqa: E731
    resumo = {"total_resolvidas": total, "aprovadas": ap, "parciais": pa, "negadas": ne,
              "pct_aprovadas": pc(ap), "pct_parciais": pc(pa), "pct_negadas": pc(ne),
              "taxa_atendimento_quantidade_pct": round(a_ * 100.0 / s_, 1) if s_ else None}
    colunas = [ColunaOut(chave="material", titulo="Material"), ColunaOut(chave="unidade", titulo="Unidade"),
               ColunaOut(chave="requisicoes", titulo="Requisições", tipo="inteiro"),
               ColunaOut(chave="aprovadas", titulo="Aprovadas", tipo="inteiro"),
               ColunaOut(chave="parciais", titulo="Parciais", tipo="inteiro"),
               ColunaOut(chave="negadas", titulo="Negadas", tipo="inteiro"),
               ColunaOut(chave="solicitado", titulo="Solicitado", tipo="numero"),
               ColunaOut(chave="atendido", titulo="Atendido", tipo="numero"),
               ColunaOut(chave="deficit_acumulado", titulo="Déficit acumulado", tipo="numero"),
               ColunaOut(chave="pct_aprovadas", titulo="% aprovadas", tipo="percentual"),
               ColunaOut(chave="taxa_atendimento_qtd", titulo="% atendido (quantidade)", tipo="percentual")]
    mapa = {"deficit": deficit, "requisicoes": func.count(), "material": Material.nome, "taxa": func.coalesce(taxa_qtd, 0)}
    return await _tabela(db, "Taxa de atendimento", colunas, q, _ordem(mapa, ordenar, direcao, Material.nome),
                         pagina, tamanho, formato, "taxa-atendimento", resumo)


# ---------------------------------------------------------------- 5) desempenho do processamento

@router.get("/relatorios/desempenho", response_model=TabelaOut)
async def relatorio_desempenho(
    de: date | None = None, ate: date | None = None,
    ordenar: str = "processadas", direcao: Direcao = "desc",
    pagina: int = Query(default=1, ge=1), tamanho: int = Query(default=20, ge=1, le=200),
    formato: Formato = "json",
    db: AsyncSession = Depends(get_db), _: Usuario = Depends(gestor_ou_almoxarife),
):
    """Espera na fila (processado_em - criado_em) e requisições processadas por worker."""
    espera = extract("epoch", Requisicao.processado_em - Requisicao.criado_em)
    base = lambda q: _filtrar(q, de, ate).where(  # noqa: E731
        Requisicao.processado_em.is_not(None), Requisicao.status.in_(RESOLVIDAS))
    q = base(select(func.coalesce(Requisicao.worker_id, "—").label("worker"), func.count().label("processadas"),
                    func.avg(espera).label("espera_media_s"),
                    func.percentile_cont(0.95).within_group(espera).label("espera_p95_s"),
                    func.max(espera).label("espera_max_s")).group_by(Requisicao.worker_id))
    t, media, p50, p95, mx = (await db.execute(base(select(
        func.count(), func.avg(espera), func.percentile_cont(0.5).within_group(espera),
        func.percentile_cont(0.95).within_group(espera), func.max(espera))))).one()
    f = lambda v: round(float(v), 3) if v is not None else None  # noqa: E731
    resumo = {"processadas": t, "espera_media_s": f(media), "espera_p50_s": f(p50), "espera_p95_s": f(p95), "espera_max_s": f(mx)}
    colunas = [ColunaOut(chave="worker", titulo="Worker"), ColunaOut(chave="processadas", titulo="Processadas", tipo="inteiro"),
               ColunaOut(chave="espera_media_s", titulo="Espera média (s)", tipo="numero"),
               ColunaOut(chave="espera_p95_s", titulo="Espera p95 (s)", tipo="numero"),
               ColunaOut(chave="espera_max_s", titulo="Espera máxima (s)", tipo="numero")]
    mapa = {"processadas": func.count(), "worker": func.coalesce(Requisicao.worker_id, "—"), "espera_media": func.avg(espera)}
    resp = await _tabela(db, "Desempenho do processamento", colunas, q, _ordem(mapa, ordenar, direcao),
                         pagina, tamanho, formato, "desempenho", resumo)
    if isinstance(resp, TabelaOut):
        for it in resp.itens:
            for k in ("espera_media_s", "espera_p95_s", "espera_max_s"):
                it[k] = f(it[k])
    return resp


# ---------------------------------------------------------------- dashboard completo

@router.get("/dashboard/completo")
async def dashboard_completo(
    periodo: Literal["7", "30", "todos"] = "30",
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user),
):
    """Tudo o que a tela inicial precisa em uma chamada. Engenheiro: só a própria obra."""
    eng = usuario.perfil == Perfil.ENGENHEIRO
    obra_id = _escopo_obra(usuario, None)
    hoje = datetime.now(timezone.utc).date()   # os timestamps do projeto são UTC naive: o "dia" também é UTC
    de = hoje - timedelta(days=int(periodo) - 1) if periodo != "todos" else None
    cont = lambda st: func.count().filter(Requisicao.status == st)  # noqa: E731

    total_mat = (await db.execute(select(func.count()).select_from(Material))).scalar_one()
    alerta = (await db.execute(select(func.count()).select_from(Material)
                               .where(Material.quantidade_estoque <= Material.estoque_critico))).scalar_one()
    abertas = (await db.execute(_filtrar(select(func.count()).select_from(Requisicao)
                                         .where(Requisicao.status.in_(ABERTAS)), None, None, obra_id))).scalar_one()

    # status no período
    por_status = {s.value: 0 for s in S}
    q = _filtrar(select(Requisicao.status, func.count()).group_by(Requisicao.status), de, None, obra_id)
    for st, n in (await db.execute(q)).all():
        por_status[st.value] = n
    resolvidas = sum(por_status[s.value] for s in RESOLVIDAS)
    taxa = round(por_status["aprovada"] * 100.0 / resolvidas, 1) if resolvidas else None
    incompletas = por_status["parcial"] + por_status["negada"]

    # requisições por dia (preenche dias sem pedidos com zero quando o período é fixo)
    dia = func.date_trunc("day", Requisicao.criado_em)
    linhas = (await db.execute(_filtrar(select(dia, func.count()).group_by(dia).order_by(dia), de, None, obra_id))).all()
    contagem = {d.date(): n for d, n in linhas}
    if de:
        por_dia = [{"data": (de + timedelta(days=i)).isoformat(), "total": contagem.get(de + timedelta(days=i), 0)}
                   for i in range((hoje - de).days + 1)]
    else:
        por_dia = [{"data": d.isoformat(), "total": n} for d, n in sorted(contagem.items())]

    # top 5 materiais consumidos e consumo por obra
    qtd = func.coalesce(func.sum(Requisicao.quantidade_atendida), 0.0)
    q_top = (select(Material.nome, Material.unidade, qtd).select_from(Requisicao)
             .join(Material, Material.id == Requisicao.material_id)
             .where(Requisicao.status.in_((S.APROVADA, S.PARCIAL))).group_by(Material.id, Material.nome, Material.unidade)
             .order_by(qtd.desc(), Material.nome).limit(5))
    top = [{"material": n, "unidade": u, "quantidade": float(q_)} for n, u, q_ in
           (await db.execute(_filtrar(q_top, de, None, obra_id))).all()]
    q_obra = (select(Obra.nome, func.count()).select_from(Requisicao).join(Obra, Obra.id == Requisicao.obra_id)
              .where(Requisicao.status.in_((S.APROVADA, S.PARCIAL))).group_by(Obra.id, Obra.nome)
              .order_by(func.count().desc(), Obra.nome).limit(10))
    por_obra = [{"obra": n, "itens_atendidos": c} for n, c in (await db.execute(_filtrar(q_obra, de, None, obra_id))).all()]

    # alertas de estoque crítico
    falta = func.greatest(Material.estoque_critico - Material.quantidade_estoque, 0.0)
    alertas = [{"material_id": i, "nome": n, "unidade": u, "estoque": e, "critico": c, "falta_para_critico": float(f)}
               for i, n, u, e, c, f in (await db.execute(
                   select(Material.id, Material.nome, Material.unidade, Material.quantidade_estoque,
                          Material.estoque_critico, falta)
                   .where(Material.quantidade_estoque <= Material.estoque_critico)
                   .order_by(Material.quantidade_estoque / func.nullif(Material.estoque_critico, 0), Material.nome)
                   .limit(10))).all()]

    # materiais em déficit (demanda aberta > estoque): visão global, só gestor/almoxarife
    em_deficit = None
    if not eng:
        dem = (select(Requisicao.material_id.label("mid"), func.sum(Requisicao.quantidade_solicitada).label("d"))
               .where(Requisicao.status.in_(ABERTAS)).group_by(Requisicao.material_id).subquery())
        em_deficit = (await db.execute(select(func.count()).select_from(Material)
                                       .join(dem, dem.c.mid == Material.id)
                                       .where(dem.c.d > Material.quantidade_estoque))).scalar_one()

    return {
        "periodo": periodo, "de": de.isoformat() if de else None,
        "cards": {"total_materiais": total_mat, "materiais_alerta": alerta, "requisicoes_abertas": abertas,
                  "taxa_atendimento_pct": taxa, "materiais_em_deficit": em_deficit,
                  "requisicoes_incompletas_periodo": incompletas,
                  "fila_pendente": None if eng else fila_global.tamanho()},   # fila global: só gestor/almoxarife
        "por_dia": por_dia, "por_status": por_status, "top_materiais": top, "consumo_por_obra": por_obra,
        "alertas": alertas,
    }
