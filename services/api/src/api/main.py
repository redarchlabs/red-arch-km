"""FastAPI application entry point with lifespan management."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import get_settings
from api.db import dispose_engine, get_engine
from api.middleware.request_logging import RequestLoggingMiddleware
from api.observability import setup_observability
from api.routers import (
    auth,
    chat,
    dimensions,
    documents,
    folders,
    health,
    memberships,
    orgs,
    search,
    tags,
    users,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    engine = get_engine(settings)

    setup_observability(
        app,
        engine,
        service_name="red-arch-km-api",
        log_level=settings.log_level,
    )

    logger.info("Starting Red Arch KM API")

    yield

    logger.info("Shutting down Red Arch KM API")
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Red Arch Knowledge Management API",
        version="2.0.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(orgs.router, prefix="/api/orgs", tags=["orgs"])
    app.include_router(users.router, prefix="/api/users", tags=["users"])
    app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
    app.include_router(folders.router, prefix="/api/folders", tags=["folders"])
    app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(search.router, prefix="/api/search", tags=["search"])
    app.include_router(dimensions.router, prefix="/api/dimensions", tags=["dimensions"])
    app.include_router(memberships.router, prefix="/api/memberships", tags=["memberships"])

    return app


app = create_app()
