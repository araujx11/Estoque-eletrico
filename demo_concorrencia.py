"""
Demonstração do cenário do enunciado:

    Estoque: 100 metros de cabo 10 mm²
    Obra A pede 40m / Obra B pede 30m / Obra C pede 50m (ao mesmo tempo)
    Total pedido: 120m > 100m disponíveis -> alguém fica com déficit

Rode com o servidor já no ar (uvicorn app.main:app) e o banco vazio:

    python demo_concorrencia.py
"""
import asyncio
import httpx

BASE_URL = "http://localhost:8000"


async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
        # 1. cadastra o material com 100m em estoque
        material = (await client.post("/materiais", json={
            "nome": "Cabo 10mm2",
            "unidade": "m",
            "quantidade_estoque": 100,
            "estoque_critico": 20,
        })).json()

        # 2. cadastra as 3 obras com prioridades diferentes
        obra_a = (await client.post("/obras", json={
            "nome": "Obra A", "cidade": "Recife", "nivel_prioridade": 2,
        })).json()
        obra_b = (await client.post("/obras", json={
            "nome": "Obra B", "cidade": "Jaboatão", "nivel_prioridade": 3,
        })).json()
        obra_c = (await client.post("/obras", json={
            "nome": "Obra C", "cidade": "Olinda",
            "nivel_prioridade": 1, "parada_por_falta_material": True,  # mais urgente
        })).json()

        pedidos = [
            (obra_a["id"], 40, "Obra A"),
            (obra_b["id"], 30, "Obra B"),
            (obra_c["id"], 50, "Obra C"),
        ]

        # 3. dispara as 3 requisições SIMULTANEAMENTE (é aqui que a race condition aconteceria
        #    sem o lock no banco)
        async def pedir(obra_id, qtd, nome):
            resp = await client.post("/requisicoes", json={
                "obra_id": obra_id,
                "material_id": material["id"],
                "quantidade_solicitada": qtd,
            })
            return nome, resp.json()

        resultados = await asyncio.gather(*[pedir(*p) for p in pedidos])

        print("\nRequisições criadas (ainda pendentes, processando em paralelo)...")
        for nome, r in resultados:
            print(f"  {nome}: id={r['id']} status={r['status']}")

        # 4. espera os workers processarem e consulta o resultado final
        await asyncio.sleep(2)

        print("\n--- RESULTADO FINAL ---")
        for nome, r in resultados:
            final = (await client.get(f"/requisicoes/{r['id']}")).json()
            print(f"  {nome}: solicitado={final['quantidade_solicitada']}m "
                  f"atendido={final['quantidade_atendida']}m status={final['status']}")

        estoque_final = (await client.get("/materiais")).json()[0]
        print(f"\nEstoque restante: {estoque_final['quantidade_estoque']}m "
              f"(deve ser >= 0, nunca negativo)")


if __name__ == "__main__":
    asyncio.run(main())
