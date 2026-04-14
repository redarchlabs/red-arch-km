"""Brain API FastAPI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from brain_api.config import BrainAPISettings
from brain_api.observability import setup_observability
from brain_api.routers import health, ingest, rag, search
from brain_api.stores import close_stores, get_stores

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    logger.info("Starting Brain API")

    # Eagerly initialize stores so startup failures surface immediately
    # (e.g. Qdrant unreachable, Neo4j auth failure).
    try:
        stores = get_stores()
        # Touch each store property to trigger lazy init
        _ = stores.embedder
        _ = stores.vector
        _ = stores.graph
        logger.info("Brain API stores initialized")
    except Exception as e:
        logger.error("Failed to initialize brain-api stores: %s", e)
        raise

    yield

    logger.info("Shutting down Brain API")
    await close_stores()


def create_app() -> FastAPI:
    settings = BrainAPISettings()  # type: ignore[call-arg]

    app = FastAPI(
        title="Red Arch Brain API",
        version="2.0.0",
        docs_url="/docs" if settings.debug else None,
        lifespan=lifespan,
    )

    # Observability must be wired before startup — the Prometheus
    # instrumentator adds middleware, which Starlette forbids once the
    # app has entered the lifespan context.
    setup_observability(
        app, service_name="red-arch-km-brain-api", log_level=settings.log_level
    )

    app.include_router(health.router)
    app.include_router(ingest.router, prefix="/api", tags=["ingest"])
    app.include_router(search.router, prefix="/api", tags=["search"])
    app.include_router(rag.router, prefix="/api/v1", tags=["rag"])

    return app


app = create_app()
