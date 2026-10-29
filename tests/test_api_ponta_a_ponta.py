"""Ponta a ponta pela API (httpx + ASGITransport): login/token, fila global e workers reais."""
import asyncio

from app.services.fila_service import iniciar_workers
from tests.helpers import esperar_processadas


async def test_cenario_do_enunciado_pela_api_com_workers(client, SessionTeste, contas):
    gestor = contas["gestor"]
    material = (await client.post("/materiais", headers=gestor, json={
        "nome": "Cabo 16mm2", "unidade": "m", "quantidade_estoque": 100, "estoque_critico": 20})).json()
    obras = [(await client.post("/obras", headers=gestor, json={"nome": f"Obra {n}", "cidade": "Recife"})).json()
             for n in "XYZ"]

    workers = await iniciar_workers(3)
    try:
        # as 3 requisições chegam ao mesmo tempo pela API
        respostas = await asyncio.gather(*[
            client.post("/requisicoes", headers=gestor, json={
                "obra_id": o["id"], "material_id": material["id"], "quantidade_solicitada": q})
            for o, q in zip(obras, (40, 30, 50))])
        assert all(r.status_code == 202 and r.json()["status"] == "pendente" for r in respostas)
        ids = [r.json()["id"] for r in respostas]
        await esperar_processadas(SessionTeste, ids)
    finally:
        for w in workers:
            w.cancel()

    finais = [(await client.get(f"/requisicoes/{i}", headers=gestor)).json() for i in ids]
    estoque = next(m for m in (await client.get("/materiais", headers=gestor)).json() if m["id"] == material["id"])
    assert sum(f["quantidade_atendida"] for f in finais) == 100
    assert estoque["quantidade_estoque"] == 0
    assert sorted(f["status"] for f in finais) == ["aprovada", "aprovada", "parcial"]
    assert {f["worker_id"] for f in finais} <= {"worker-1", "worker-2", "worker-3"}
    assert all(f["solicitante_id"] is not None for f in finais)


async def test_login_real_e_uso_do_token_para_requisitar(client, contas):
    r = await client.post("/auth/login", data={"username": "eng@teste.com", "password": "senha-12345"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    ok = await client.post("/requisicoes", headers=h, json={
        "obra_id": contas["obra_a"]["id"], "material_id": contas["material"]["id"], "quantidade_solicitada": 3})
    assert ok.status_code == 202
    negado = await client.post("/requisicoes", headers=h, json={
        "obra_id": contas["obra_b"]["id"], "material_id": contas["material"]["id"], "quantidade_solicitada": 3})
    assert negado.status_code == 403
