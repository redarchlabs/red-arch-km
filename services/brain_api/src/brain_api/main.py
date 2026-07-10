"""Brain API FastAPI application."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from brain_api.config import BrainAPISettings
from brain_api.observability import setup_observability
from brain_api.routers import agent, health, ingest, rag, search
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
        if stores.settings.use_fact_engine:
            # Ensure the reified-claim schema (constraints + vector/fulltext
            # indexes) so the agentic path is ready on first request.
            stores.ensure_fact_schema()
            logger.info("Fact engine enabled; schema ensured")
        logger.info("Brain API stores initialized")
    except Exception as e:
        logger.error("Failed to initialize brain-api stores: %s", e)
        raise

    # Warm the actual query path (embedding + Qdrant + Neo4j + chat). Construction
    # above only builds clients; the first *real* round-trip pays connection/TLS/
    # pool setup (~20s cold vs ~3s warm) that we don't want a user's first question
    # to absorb. Best-effort and off the critical path — failures only log.
    try:
        from brain_api.services.search_service import SearchService

        warmer = SearchService(stores, stores.settings)
        await asyncio.to_thread(warmer.warm_up)
        logger.info("Brain API query path warmed")
    except Exception as e:  # noqa: BLE001 - warm-up must never block startup
        logger.warning("Brain API warm-up skipped: %s", e)

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
    setup_observability(app, service_name="red-arch-km-brain-api", log_level=settings.log_level)

    app.include_router(health.router)
    app.include_router(ingest.router, prefix="/api", tags=["ingest"])
    app.include_router(search.router, prefix="/api", tags=["search"])
    app.include_router(rag.router, prefix="/api/v1", tags=["rag"])
    app.include_router(agent.router, prefix="/api/v1", tags=["agent"])

    return app


app = create_app()
