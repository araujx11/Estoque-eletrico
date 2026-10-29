import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import exigir_perfis, get_current_user
from app.core.database import get_db
from app.models import (
    Material, MovimentacaoEstoque, Obra, Perfil, Requisicao, StatusObra, StatusRequisicao, TipoMovimentacao, Usuario,
)
from app.schemas import (
    DashboardOut,
    MaterialCreate, MaterialOut,
    ObraCreate, ObraOut,
    AndamentoUpdate, PedidoCreate, RequisicaoCreate, RequisicaoOut,
    ResumoOut, Unidade, UNIDADES_DESCRICAO,
)
from app.services import auditoria_service
from app.services.fila_service import fila_global

router = APIRouter()

# Quem pode o quê (validado aqui no backend; o frontend só esconde botões por conveniência)
somente_gestor = exigir_perfis(Perfil.GESTOR)
gestor_ou_almoxarife = exigir_perfis(Perfil.GESTOR, Perfil.ALMOXARIFE)
pode_requisitar = exigir_perfis(Perfil.GESTOR, Perfil.ENGENHEIRO)


def _checar_obra(usuario: Usuario, obra_id: int):
    """Engenheiro só atua na própria obra (checado antes de consultar a obra, p/ não vazar existência)."""
    if usuario.perfil == Perfil.ENGENHEIRO and usuario.obra_id != obra_id:
        raise HTTPException(403, "Você só pode operar na sua própria obra")


# ---------- Unidades / Resumo ----------

@router.get("/unidades")
async def listar_unidades(_: Usuario = Depends(get_current_user)):
    return [{"valor": u.value, "descricao": UNIDADES_DESCRICAO[u]} for u in Unidade]


@router.get("/resumo", response_model=ResumoOut)
async def resumo(db: AsyncSession = Depends(get_db), _: Usuario = Depends(gestor_ou_almoxarife)):
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
async def criar_material(
    payload: MaterialCreate, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(gestor_ou_almoxarife)
):
    dados = payload.model_dump()
    dados["unidade"] = payload.unidade.value
    if (await db.execute(select(Material).where(Material.nome == payload.nome))).first():
        raise HTTPException(409, "Já existe um material com esse nome")
    material = Material(**dados)
    db.add(material)
    await db.flush()
    if material.quantidade_estoque > 0:
        # o livro-razão começa com o saldo inicial, senão "inicial + entradas - saídas" não fecharia
        db.add(MovimentacaoEstoque(
            material_id=material.id, tipo=TipoMovimentacao.ENTRADA, quantidade=material.quantidade_estoque,
            saldo_apos=material.quantidade_estoque, usuario_id=usuario.id, observacao="Estoque inicial",
        ))
    auditoria_service.registrar(db, usuario.id, "material_criado", "material", material.id, {
        "nome": material.nome, "unidade": material.unidade, "estoque_inicial": material.quantidade_estoque,
        "estoque_critico": material.estoque_critico,
    })
    await db.commit()
    await db.refresh(material)
    return material


@router.get("/materiais", response_model=list[MaterialOut])
async def listar_materiais(
    busca: str | None = Query(default=None, max_length=100, description="texto no nome do material"),
    somente_criticos: bool = False,
    db: AsyncSession = Depends(get_db), _: Usuario = Depends(get_current_user),
):
    consulta = select(Material).order_by(Material.nome)
    if busca:
        consulta = consulta.where(Material.nome.ilike(f"%{busca.strip()}%"))
    if somente_criticos:
        consulta = consulta.where(Material.quantidade_estoque <= Material.estoque_critico)
    return (await db.execute(consulta)).scalars().all()


@router.put("/materiais/{material_id}", response_model=MaterialOut)
async def editar_material(
    material_id: int, payload: MaterialCreate,
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(gestor_ou_almoxarife),
):
    """Edita dados do material. Trava a linha (FOR UPDATE) como os workers, para o ajuste não se
    perder se uma requisição do mesmo material estiver sendo processada. Se a quantidade mudar,
    a diferença vira uma movimentação de AJUSTE (prefira /movimentacoes/entrada e /ajuste, que pedem o motivo)."""
    material = (await db.execute(
        select(Material).where(Material.id == material_id).with_for_update()
    )).scalar_one_or_none()
    if material is None:
        raise HTTPException(404, "Material não encontrado")
    duplicado = (await db.execute(
        select(Material.id).where(Material.nome == payload.nome, Material.id != material_id)
    )).first()
    if duplicado:
        raise HTTPException(409, "Já existe um material com esse nome")
    antes = {"nome": material.nome, "unidade": material.unidade, "estoque": material.quantidade_estoque,
             "estoque_critico": material.estoque_critico}
    delta = payload.quantidade_estoque - material.quantidade_estoque
    material.nome = payload.nome
    material.unidade = payload.unidade.value
    material.quantidade_estoque = payload.quantidade_estoque
    material.estoque_critico = payload.estoque_critico
    if abs(delta) > 1e-9:
        db.add(MovimentacaoEstoque(
            material_id=material.id, tipo=TipoMovimentacao.AJUSTE, quantidade=delta,
            saldo_apos=material.quantidade_estoque, usuario_id=usuario.id,
            observacao="Ajuste feito pela edição do material",
        ))
    auditoria_service.registrar(db, usuario.id, "material_editado", "material", material.id, {
        "antes": antes, "depois": {"nome": material.nome, "unidade": material.unidade,
                                   "estoque": material.quantidade_estoque, "estoque_critico": material.estoque_critico},
    })
    await db.commit()
    await db.refresh(material)
    return material


async def _remover(db: AsyncSession, entidade, coluna_req, forcar: bool, usuario: Usuario, acao: str):
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
    if isinstance(entidade, Material):
        # o histórico de movimentações acompanha o material removido
        await db.execute(delete(MovimentacaoEstoque).where(MovimentacaoEstoque.material_id == entidade.id))
    elif historico:
        # as saídas dos pedidos apagados ficam no livro-razão do material, só perdem o vínculo com a requisição
        ids_req = select(Requisicao.id).where(coluna_req == entidade.id)
        await db.execute(update(MovimentacaoEstoque)
                         .where(MovimentacaoEstoque.requisicao_id.in_(ids_req)).values(requisicao_id=None))
    if historico:
        await db.execute(delete(Requisicao).where(coluna_req == entidade.id))
    detalhes = {"nome": entidade.nome, "requisicoes_removidas": historico}
    entidade_nome, entidade_id = type(entidade).__name__.lower(), entidade.id
    await db.delete(entidade)
    auditoria_service.registrar(db, usuario.id, acao, entidade_nome, entidade_id, detalhes)
    await db.commit()


@router.delete("/materiais/{material_id}", status_code=204)
async def remover_material(
    material_id: int, forcar: bool = False,
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(somente_gestor),
):
    material = await db.get(Material, material_id)
    if material is None:
        raise HTTPException(404, "Material não encontrado")
    await _remover(db, material, Requisicao.material_id, forcar, usuario, "material_removido")


# ---------- Obras ----------

@router.delete("/obras/{obra_id}", status_code=204)
async def remover_obra(
    obra_id: int, forcar: bool = False,
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(somente_gestor),
):
    obra = await db.get(Obra, obra_id)
    if obra is None:
        raise HTTPException(404, "Obra não encontrada")
    vinculados = (await db.execute(
        select(func.count()).select_from(Usuario).where(Usuario.obra_id == obra_id)
    )).scalar_one()
    if vinculados:
        raise HTTPException(409, f"Há {vinculados} usuário(s) vinculados a esta obra. Desvincule-os antes de remover.")
    await _remover(db, obra, Requisicao.obra_id, forcar, usuario, "obra_removida")


@router.patch("/obras/{obra_id}/andamento", response_model=ObraOut)
async def atualizar_andamento(
    obra_id: int, payload: AndamentoUpdate,
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(somente_gestor),
):
    """Atualiza progresso (0-100) e descrição. progresso=100 ou finalizada=true encerra a obra;
    diminuir o progresso de uma obra finalizada a reabre."""
    obra = await db.get(Obra, obra_id)
    if obra is None:
        raise HTTPException(404, "Obra não encontrada")
    finalizar = payload.finalizada or payload.progresso == 100
    if finalizar:
        obra.descricao_andamento = None  # obra concluída: o comentário de andamento perde o sentido
        obra.progresso = 100
        obra.parada_por_falta_material = False
        if obra.status != StatusObra.FINALIZADA.value:
            obra.status = StatusObra.FINALIZADA.value
            obra.finalizada_em = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        obra.descricao_andamento = payload.descricao_andamento
        obra.progresso = payload.progresso
        obra.status = StatusObra.EM_ANDAMENTO.value
        obra.finalizada_em = None
    auditoria_service.registrar(db, usuario.id, "obra_andamento", "obra", obra.id, {
        "nome": obra.nome, "progresso": obra.progresso, "status": obra.status,
    })
    await db.commit()
    await db.refresh(obra)
    return obra

@router.post("/obras", response_model=ObraOut)
async def criar_obra(payload: ObraCreate, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(somente_gestor)):
    obra = Obra(**payload.model_dump())
    db.add(obra)
    await db.flush()
    auditoria_service.registrar(db, usuario.id, "obra_criada", "obra", obra.id, {
        "nome": obra.nome, "cidade": obra.cidade, "nivel_prioridade": obra.nivel_prioridade,
    })
    await db.commit()
    await db.refresh(obra)
    return obra


@router.get("/obras", response_model=list[ObraOut])
async def listar_obras(
    busca: str | None = Query(default=None, max_length=100, description="texto no nome ou na cidade"),
    status: StatusObra | None = None,
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user),
):
    """Gestor/almoxarife veem todas; engenheiro vê apenas a própria obra."""
    consulta = select(Obra).order_by(Obra.id)
    if busca:
        like = f"%{busca.strip()}%"
        consulta = consulta.where(Obra.nome.ilike(like) | Obra.cidade.ilike(like))
    if status:
        consulta = consulta.where(Obra.status == status.value)
    if usuario.perfil == Perfil.ENGENHEIRO:
        consulta = consulta.where(Obra.id == usuario.obra_id)
    return (await db.execute(consulta)).scalars().all()


# ---------- Requisições ----------

@router.post("/requisicoes", response_model=RequisicaoOut, status_code=202)
async def criar_requisicao(
    payload: RequisicaoCreate, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(pode_requisitar)
):
    """
    Cria a requisição com status PENDENTE e a coloca na fila.
    A resposta 202 (Accepted) reflete que o processamento é assíncrono:
    o cliente deve consultar GET /requisicoes/{id} para saber o resultado.
    """
    _checar_obra(usuario, payload.obra_id)
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
        solicitante_id=usuario.id,
    )
    db.add(requisicao)
    await db.flush()
    auditoria_service.registrar(db, usuario.id, "requisicao_criada", "requisicao", requisicao.id, {
        "obra_id": obra.id, "obra": obra.nome, "material_id": material.id, "material": material.nome,
        "quantidade": payload.quantidade_solicitada,
    })
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


@router.post("/pedidos", response_model=list[RequisicaoOut], status_code=202)
async def criar_pedido(
    payload: PedidoCreate, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(pode_requisitar)
):
    """Uma requisição com vários materiais: gera 1 Requisicao por item, todas com o mesmo pedido_id."""
    _checar_obra(usuario, payload.obra_id)
    obra = await db.get(Obra, payload.obra_id)
    if obra is None:
        raise HTTPException(404, "Obra não encontrada")
    if obra.status == StatusObra.FINALIZADA.value:
        raise HTTPException(409, "Obra finalizada não aceita novas requisições")
    for item in payload.itens:
        if await db.get(Material, item.material_id) is None:
            raise HTTPException(404, f"Material {item.material_id} não encontrado")

    pedido_id = str(uuid.uuid4())
    requisicoes = [
        Requisicao(obra_id=obra.id, material_id=i.material_id,
                   quantidade_solicitada=i.quantidade_solicitada, pedido_id=pedido_id,
                   solicitante_id=usuario.id)
        for i in payload.itens
    ]
    db.add_all(requisicoes)
    auditoria_service.registrar(db, usuario.id, "pedido_criado", "pedido", pedido_id, {
        "obra_id": obra.id, "obra": obra.nome,
        "itens": [{"material_id": i.material_id, "quantidade": i.quantidade_solicitada} for i in payload.itens],
    })
    await db.commit()
    for r in requisicoes:
        await db.refresh(r)
        await fila_global.adicionar(
            requisicao_id=r.id,
            parada_por_falta_material=obra.parada_por_falta_material,
            nivel_prioridade=obra.nivel_prioridade,
            prazo_entrega=obra.prazo_entrega,
            criado_em=r.criado_em or datetime.now(timezone.utc),
        )
    return requisicoes


@router.get("/requisicoes/{requisicao_id}", response_model=RequisicaoOut)
async def status_requisicao(
    requisicao_id: str, db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user)
):
    requisicao = await db.get(Requisicao, requisicao_id)
    if requisicao is None:
        raise HTTPException(404, "Requisição não encontrada")
    _checar_obra(usuario, requisicao.obra_id)
    return requisicao


@router.get("/requisicoes", response_model=list[RequisicaoOut])
async def listar_requisicoes(
    status: StatusRequisicao | None = None,
    obra_id: int | None = None,
    material_id: int | None = None,
    busca: str | None = Query(default=None, max_length=100, description="texto no nome da obra, do material ou no worker"),
    de: date | None = None,
    ate: date | None = None,
    limite: int | None = Query(default=None, ge=1, le=1000),
    db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user),
):
    """Gestor/almoxarife veem todas; engenheiro vê apenas as da sua obra. Aceita filtros e busca."""
    consulta = select(Requisicao).order_by(Requisicao.criado_em.desc())
    if usuario.perfil == Perfil.ENGENHEIRO:
        consulta = consulta.where(Requisicao.obra_id == usuario.obra_id)
    if status:
        consulta = consulta.where(Requisicao.status == status)
    if obra_id:
        consulta = consulta.where(Requisicao.obra_id == obra_id)
    if material_id:
        consulta = consulta.where(Requisicao.material_id == material_id)
    if de:
        consulta = consulta.where(Requisicao.criado_em >= datetime.combine(de, datetime.min.time()))
    if ate:
        consulta = consulta.where(Requisicao.criado_em < datetime.combine(ate, datetime.min.time()) + timedelta(days=1))
    if busca:
        like = f"%{busca.strip()}%"
        consulta = (consulta.join(Obra, Obra.id == Requisicao.obra_id).join(Material, Material.id == Requisicao.material_id)
                    .where(Obra.nome.ilike(like) | Material.nome.ilike(like) | Requisicao.worker_id.ilike(like)))
    if limite:
        consulta = consulta.limit(limite)
    return (await db.execute(consulta)).scalars().all()


# ---------- Dashboard ----------

@router.get("/dashboard/resumo", response_model=DashboardOut)
async def dashboard_resumo(db: AsyncSession = Depends(get_db), usuario: Usuario = Depends(get_current_user)):
    """Números do painel inicial. Para engenheiro, obras e requisições são só as da sua obra."""
    eh_eng = usuario.perfil == Perfil.ENGENHEIRO

    q_status = select(Requisicao.status, func.count()).group_by(Requisicao.status)
    q_obras = select(func.count()).select_from(Obra)
    if eh_eng:
        q_status = q_status.where(Requisicao.obra_id == usuario.obra_id)
        q_obras = q_obras.where(Obra.id == usuario.obra_id)
    por_status = {s.value: 0 for s in StatusRequisicao}
    for status, n in (await db.execute(q_status)).all():
        por_status[status.value] = n

    materiais = (await db.execute(select(Material).order_by(Material.nome))).scalars().all()
    criticos = [m for m in materiais if m.quantidade_estoque <= m.estoque_critico]
    return DashboardOut(
        total_materiais=len(materiais),
        materiais_criticos=len(criticos),
        lista_criticos=criticos,
        total_obras=(await db.execute(q_obras)).scalar_one(),
        total_requisicoes=sum(por_status.values()),
        por_status=por_status,
        fila_pendente=fila_global.tamanho(),
    )
