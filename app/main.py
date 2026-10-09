from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.database import init_models
from app.api.routes import router
from app.services.fila_service import iniciar_workers, reenfileirar_pendentes

N_WORKERS = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    await reenfileirar_pendentes()
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

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(router)

FRONT_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/health")
async def health():
    return {"status": "ok", "workers_ativos": N_WORKERS}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(FRONT_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONT_DIR), name="static")
