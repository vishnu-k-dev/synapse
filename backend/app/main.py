from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import apps, graph, jobs, synthesis, tools, workflows
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.engine import close_engine
from app.graph.client import close_driver, get_driver


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # Warm up connections
    get_driver()
    yield
    # Graceful shutdown
    await close_driver()
    await close_engine()


settings = get_settings()

app = FastAPI(
    title="SYNAPSE API",
    description="Semantic Protocol Synthesis Engine for AI Agents",
    version="0.1.0",
    docs_url="/docs" if settings.app_debug else None,
    redoc_url="/redoc" if settings.app_debug else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(apps.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")
app.include_router(tools.router, prefix="/api/v1")
app.include_router(workflows.router, prefix="/api/v1")
app.include_router(synthesis.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/readiness")
async def readiness() -> dict:
    from sqlalchemy import text
    from app.db.engine import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
