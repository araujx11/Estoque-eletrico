"""
DADOS FICTÍCIOS DE DEMONSTRAÇÃO — não representam nenhuma empresa nem obra reais.

Popula o banco apontado por DATABASE_URL (.env) com materiais elétricos, 3 obras, usuários de cada perfil
(senha de demonstração: demo12345), ~30 dias de requisições e movimentações já processadas e alguns pedidos
ABERTOS para o Diagnóstico de déficit mostrar 🟢 🟡 🔴.

    python -m scripts.seed_demo            # idempotente: se os dados de demo já existem, não faz nada
    python -m scripts.seed_demo --reset    # APAGA TUDO do banco apontado e recria a demo (pede confirmação)

Para ver os pedidos abertos sem que os workers os processem na hora, suba a API com N_WORKERS=0:
    $env:N_WORKERS = "0"; uvicorn app.main:app
Depois reinicie com N_WORKERS=1 para ver o processamento seguir EXATAMENTE a previsão do diagnóstico (com vários workers,
dois pedidos do mesmo material podem trocar de ordem: ver docs/achados.md, achado 14).
"""
import argparse
import asyncio
import random
import sys
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.database import AsyncSessionLocal, Base, DATABASE_URL, engine, init_models
from app.core.security import hash_senha
from app.models import (
    LogAuditoria, Material, MovimentacaoEstoque, Obra, Perfil, Requisicao, StatusRequisicao as S,
    TipoMovimentacao, Usuario,
)

SENHA_DEMO = "demo12345"
EMAIL_MARCADOR = "gestor@demo.local"       # existe => a demo já foi criada

# (nome, unidade, crítico, estoque inicial, qtd mín, qtd máx, peso de consumo)
MATERIAIS = [
    ("Cabo flexível 1,5 mm²", "m", 200, 1500, 20, 120, 5), ("Cabo flexível 2,5 mm²", "m", 300, 2000, 20, 150, 6),
    ("Cabo flexível 4 mm²", "m", 200, 1200, 20, 100, 4), ("Cabo flexível 6 mm²", "m", 150, 900, 10, 80, 3),
    ("Cabo 10 mm²", "m", 20, 600, 10, 60, 3), ("Cabo 16 mm²", "m", 100, 500, 10, 50, 2),
    ("Disjuntor monopolar 16 A", "un", 20, 120, 2, 12, 4), ("Disjuntor monopolar 20 A", "un", 30, 200, 2, 12, 4),
    ("Disjuntor bipolar 32 A", "un", 15, 90, 1, 8, 3), ("Disjuntor tripolar 50 A", "un", 10, 60, 1, 6, 2),
    ("DR 40 A 30 mA", "un", 8, 40, 1, 4, 2),
    ('Eletroduto PVC rígido 3/4"', "barra", 40, 300, 5, 30, 4), ('Eletroduto PVC rígido 1"', "barra", 40, 250, 5, 30, 3),
    ("Eletroduto corrugado 25 mm", "rolo", 10, 60, 1, 6, 3),
    ("Tomada 2P+T 10 A", "un", 40, 300, 5, 30, 4), ("Tomada 2P+T 20 A", "un", 30, 200, 4, 24, 3),
    ("Interruptor simples", "un", 30, 220, 4, 24, 3),
    ("Quadro de distribuição 12 disjuntores", "un", 3, 25, 1, 3, 2), ("Quadro de distribuição 24 disjuntores", "un", 2, 15, 1, 2, 1),
    ("Conector de emenda 6 mm²", "un", 100, 800, 20, 100, 3), ("Terminal de compressão 10 mm²", "un", 100, 700, 20, 100, 3),
    ("Terminal olhal 6 mm²", "un", 100, 600, 20, 100, 2),
    ("Lâmpada LED 9 W", "un", 50, 400, 10, 60, 4), ("Lâmpada LED 18 W", "un", 40, 300, 10, 50, 3),
    ("Refletor LED 50 W", "un", 10, 60, 1, 6, 1),
    ("Parafuso com bucha 6 mm (cx 100)", "cx", 10, 80, 1, 8, 2), ("Fita isolante 20 m", "rolo", 12, 100, 2, 12, 3),
]

# materiais ESCASSOS: começam com pouco estoque e só são repostos no fim do período, então o histórico
# tem pedidos parciais e negados (senão tudo seria aprovado e os relatórios ficariam sem graça)
ESCASSOS = {"Cabo flexível 6 mm²": 200, "Disjuntor tripolar 50 A": 15, "DR 40 A 30 mA": 10,
            "Terminal olhal 6 mm²": 250, "Refletor LED 50 W": 12, "Quadro de distribuição 24 disjuntores": 3}
DIA_REPOSICAO_ESCASSOS = 24

# nome da obra, cidade, prioridade, dias até o prazo, parada, progresso, descrição
OBRAS = [
    ("Edifício Atlântico", "Recife", 1, 20, False, 45, "Cabeamento dos pavimentos 5 a 8 em andamento"),
    ("Residencial Jaboatão Park", "Jaboatão", 3, 45, False, 20, "Eletrodutos da fundação concluídos"),
    ("Galpão Logístico Olinda", "Olinda", 2, 10, True, 70, "Obra parada aguardando cabos e quadros"),
]

# estoque FINAL desejado (aplicado por um AJUSTE de inventário) e pedidos ABERTOS, para o diagnóstico
# material -> (estoque final, [(obra, quantidade)])
CENARIO = {
    "Cabo 10 mm²": (100, [("Galpão Logístico Olinda", 50), ("Edifício Atlântico", 40), ("Residencial Jaboatão Park", 30)]),  # 🔴 déficit 20 m
    "Disjuntor monopolar 20 A": (150, [("Edifício Atlântico", 40)]),                                                            # 🟢
    'Eletroduto PVC rígido 3/4"': (60, [("Residencial Jaboatão Park", 30)]),                                                     # 🟡
    "Quadro de distribuição 12 disjuntores": (5, [("Edifício Atlântico", 4), ("Galpão Logístico Olinda", 3)]),                   # 🔴
    "Terminal de compressão 10 mm²": (0, [("Galpão Logístico Olinda", 20)]),                                                     # 🔴 zerado
    "Fita isolante 20 m": (10, []),                                                                                              # 🟡 sem demanda
    "Lâmpada LED 18 W": (30, []),                                                                                                # alerta no dashboard
}


def slug(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def confirmar_reset(auto: bool) -> bool:
    alvo = make_url(DATABASE_URL)
    print(f"\n⚠️  --reset vai APAGAR TODOS OS DADOS de: {alvo.host}:{alvo.port}/{alvo.database}")
    if auto:
        return True
    if not sys.stdin.isatty():
        print("Sem terminal interativo: use --sim para confirmar.")
        return False
    return input('Digite APAGAR para confirmar: ').strip() == "APAGAR"


async def gerar(rng: random.Random):
    hoje = agora()
    inicio = (hoje - timedelta(days=31)).replace(hour=7, minute=0, second=0, microsecond=0)
    hash_demo = await hash_senha(SENHA_DEMO)

    async with AsyncSessionLocal() as db:
        # ---- usuários e obras ----
        obras = [Obra(nome=n, cidade=c, nivel_prioridade=p, prazo_entrega=hoje + timedelta(days=d),
                      parada_por_falta_material=parada, progresso=prog, descricao_andamento=desc)
                 for n, c, p, d, parada, prog, desc in OBRAS]
        db.add_all(obras)
        await db.flush()
        por_nome_obra = {o.nome: o for o in obras}

        gestor = Usuario(nome="Gestora Demo", email=EMAIL_MARCADOR, senha_hash=hash_demo, perfil=Perfil.GESTOR)
        almox = Usuario(nome="Almoxarife Demo", email="almoxarife@demo.local", senha_hash=hash_demo, perfil=Perfil.ALMOXARIFE)
        engs = {o.nome: Usuario(nome=f"Engenharia {o.cidade} (demo)", email=f"eng.{slug(o.cidade)}@demo.local",
                                senha_hash=hash_demo, perfil=Perfil.ENGENHEIRO, obra_id=o.id) for o in obras}
        db.add_all([gestor, almox, *engs.values()])
        await db.flush()

        # ---- materiais com saldo inicial (ENTRADA) ----
        materiais, estoque = [], {}
        movs = []
        for nome, un, crit, ini, *_ in MATERIAIS:
            m = Material(nome=nome, unidade=un, quantidade_estoque=0, estoque_critico=crit)
            materiais.append(m)
        db.add_all(materiais)
        await db.flush()
        info = {m.id: spec for m, spec in zip(materiais, MATERIAIS)}
        for m in materiais:
            estoque[m.id] = float(ESCASSOS.get(m.nome, info[m.id][3]))
            movs.append(MovimentacaoEstoque(material_id=m.id, tipo=TipoMovimentacao.ENTRADA, quantidade=estoque[m.id],
                                            saldo_apos=estoque[m.id], usuario_id=almox.id,
                                            observacao="Estoque inicial (demonstração)", criado_em=inicio))

        pesos = [info[m.id][6] * (2 if m.nome in ESCASSOS else 1) for m in materiais]
        reqs = []
        ultimo = [inicio]

        def processar(req, material, quando):
            quando = max(quando, ultimo[0] + timedelta(seconds=1))   # instantes estritamente crescentes
            ultimo[0] = quando
            """Mesma regra do estoque_service: atende o que houver; zerado => negada."""
            disponivel = estoque[material.id]
            if disponivel <= 0:
                atendida, status = 0.0, S.NEGADA
            elif disponivel >= req.quantidade_solicitada:
                atendida, status = req.quantidade_solicitada, S.APROVADA
            else:
                atendida, status = disponivel, S.PARCIAL
            estoque[material.id] = disponivel - atendida
            req.quantidade_atendida, req.status = atendida, status
            req.processado_em = quando
            if atendida > 0:
                movs.append(MovimentacaoEstoque(
                    material_id=material.id, tipo=TipoMovimentacao.SAIDA, quantidade=atendida,
                    saldo_apos=estoque[material.id], requisicao_id=req.id, criado_em=quando))

        # ---- 30 dias de histórico, dia a dia ----
        for dia in range(31):
            base = inicio + timedelta(days=dia)
            # reposição: abaixo de 1,5x o crítico, repõe com ~60% de chance (às vezes falta, e aparecem parciais/negadas)
            for m in materiais:
                crit = info[m.id][2]
                if m.nome in ESCASSOS and dia < DIA_REPOSICAO_ESCASSOS:
                    continue                                   # escasso: sem reposição até o fim do período
                if estoque[m.id] < crit * 1.5 and rng.random() < 0.6:
                    qtd = float(info[m.id][3] // 2)
                    estoque[m.id] += qtd
                    movs.append(MovimentacaoEstoque(
                        material_id=m.id, tipo=TipoMovimentacao.ENTRADA, quantidade=qtd, saldo_apos=estoque[m.id],
                        usuario_id=almox.id, observacao=f"Recebimento NF {rng.randint(1000, 9999)} (demonstração)",
                        criado_em=base + timedelta(minutes=rng.randint(0, 50))))
            pedidos = sorted(rng.randint(0, 600) for _ in range(rng.randint(3, 7)))   # minutos após as 8h
            for minuto in pedidos:
                quando = base + timedelta(hours=1, minutes=minuto)
                obra = rng.choice(obras)
                pedido_id = str(uuid.uuid4())
                escolhidos = []
                while len(escolhidos) < rng.randint(1, 3):
                    m = rng.choices(materiais, weights=pesos)[0]
                    if m not in escolhidos:
                        escolhidos.append(m)
                for k, m in enumerate(escolhidos):
                    _, _, _, _, qmin, qmax, _ = info[m.id]
                    r = Requisicao(id=str(uuid.uuid4()), obra_id=obra.id, material_id=m.id, pedido_id=pedido_id,
                                   solicitante_id=engs[obra.nome].id, quantidade_solicitada=float(rng.randint(qmin, qmax)),
                                   criado_em=quando, worker_id=f"worker-{rng.randint(1, 3)}")
                    reqs.append(r)
                    processar(r, m, quando + timedelta(seconds=rng.randint(1, 6) + k))

        # ---- cenário do diagnóstico: AJUSTE de inventário até o estoque desejado + pedidos abertos ----
        por_nome = {m.nome: m for m in materiais}
        fim_historico = hoje - timedelta(hours=2)
        abertos = []
        for nome, (alvo, pedidos) in CENARIO.items():
            m = por_nome[nome]
            delta = alvo - estoque[m.id]
            if abs(delta) > 1e-9:
                estoque[m.id] = float(alvo)
                movs.append(MovimentacaoEstoque(
                    material_id=m.id, tipo=TipoMovimentacao.AJUSTE, quantidade=delta, saldo_apos=estoque[m.id],
                    usuario_id=almox.id, observacao="Contagem de inventário (demonstração)", criado_em=fim_historico))
            for i, (obra_nome, qtd) in enumerate(pedidos):
                abertos.append(Requisicao(
                    id=str(uuid.uuid4()), obra_id=por_nome_obra[obra_nome].id, material_id=m.id,
                    solicitante_id=engs[obra_nome].id, quantidade_solicitada=float(qtd), status=S.PENDENTE,
                    criado_em=hoje - timedelta(minutes=30 - i * 5)))

        # ---- gravação (movimentações em ordem cronológica => ids em ordem) ----
        db.add_all(reqs)
        await db.flush()
        movs.sort(key=lambda x: x.criado_em)
        db.add_all(movs)
        db.add_all(abertos)
        for m in materiais:
            m.quantidade_estoque = estoque[m.id]

        # ---- auditoria de exemplo ----
        logs = [LogAuditoria(usuario_id=gestor.id, acao="seed_demo", entidade="sistema",
                             detalhes={"demo": True, "mensagem": "Dados de demonstração gerados"}, criado_em=hoje)]
        for dia in range(0, 31, 3):
            for u in (gestor, almox, *engs.values()):
                if rng.random() < 0.6:
                    logs.append(LogAuditoria(usuario_id=u.id, acao="login", entidade="usuario", entidade_id=str(u.id),
                                             detalhes={"email": u.email, "demo": True},
                                             criado_em=inicio + timedelta(days=dia, hours=rng.randint(1, 9))))
        for r in rng.sample(reqs, 12):
            logs.append(LogAuditoria(usuario_id=r.solicitante_id, acao="requisicao_criada", entidade="requisicao",
                                     entidade_id=r.id, detalhes={"quantidade": r.quantidade_solicitada, "demo": True},
                                     criado_em=r.criado_em))
        db.add_all(logs)
        await db.commit()

    return {"materiais": len(materiais), "obras": len(obras), "requisicoes_historico": len(reqs),
            "requisicoes_abertas": len(abertos), "movimentacoes": len(movs)}


async def principal(a):
    await init_models()
    async with engine.begin() as conn:
        existe = (await conn.execute(text("SELECT 1 FROM usuarios WHERE email = :e"), {"e": EMAIL_MARCADOR})).scalar()
    if existe and not a.reset:
        print("Os dados de demonstração já existem (nada foi alterado). Use --reset para recriá-los do zero.")
        return
    if a.reset:
        if not await confirmar_reset(a.sim):
            print("Cancelado. Nada foi apagado.")
            return
        tabelas = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))
        print("Banco limpo.")
    resumo = await gerar(random.Random(a.semente))
    print("\n✅ Dados de DEMONSTRAÇÃO criados:", resumo)
    print(f"\nUsuários (senha de todos: {SENHA_DEMO}):")
    print(f"  gestor      {EMAIL_MARCADOR}")
    print("  almoxarife  almoxarife@demo.local")
    print("  engenheiros eng.recife@demo.local | eng.jaboatao@demo.local | eng.olinda@demo.local")
    print('\nDica: para o Diagnóstico mostrar os pedidos abertos sem processá-los, suba a API com  $env:N_WORKERS = "0"'
          '\n      depois reinicie com  $env:N_WORKERS = "1"  para o processamento seguir exatamente a previsão.')


def main():
    p = argparse.ArgumentParser(description="Dados fictícios de demonstração")
    p.add_argument("--reset", action="store_true", help="APAGA todo o banco apontado por DATABASE_URL e recria a demo")
    p.add_argument("--sim", action="store_true", help="confirma o --reset sem perguntar (use com cuidado)")
    p.add_argument("--semente", type=int, default=2026)
    a = p.parse_args()

    async def rodar():
        try:
            await principal(a)
        finally:
            await engine.dispose()
    asyncio.run(rodar())


if __name__ == "__main__":
    main()
