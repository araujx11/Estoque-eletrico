from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import init_models
from app.api.routes import router
from app.services.fila_service import iniciar_workers

N_WORKERS = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    workers = await iniciar_workers(N_WORKERS)
    yield
    for w in workers:
        w.cancel()


app = FastAPI(
    title="Controle de Estoque Paralelo - Engenharia Elétrica",
    description=(
        "Motor de alocação de materiais para obras com processamento "
        "concorrente via fila + pool de workers, e consistência garantida "
        "por lock pessimista no PostgreSQL."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/")
async def root():
    return {"status": "ok", "workers_ativos": N_WORKERS}
