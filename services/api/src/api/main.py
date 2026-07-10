"""FastAPI application entry point with lifespan management."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db import dispose_engine, get_engine, get_session_factory
from api.dependencies import close_redis_client, get_redis_client
from api.exception_handlers import make_unhandled_exception_handler
from api.middleware.request_logging import RequestLoggingMiddleware
from api.observability import setup_observability
from api.routers import (
    admin,
    agent,
    attributes,
    auth,
    chat,
    dimensions,
    documents,
    entity_definitions,
    entity_records,
    folders,
    forms,
    health,
    inbound,
    internal,
    memberships,
    migration,
    orgs,
    reports,
    search,
    setup,
    tags,
    users,
    views,
    workflows,
)
from api.services.setup_token import ensure_setup_token

logger = logging.getLogger(__name__)


async def _announce_setup_token_if_needed() -> None:
    """On boot with no active site admin, generate the one-time setup token and
    print it to the logs. Failure here (e.g. Redis or DB down/hung) must never
    prevent startup — setup is simply retried on the next boot."""
    settings = get_settings()
    try:
        async with asyncio.timeout(10):
            factory = get_session_factory(settings)
            async with factory() as session:
                token = await ensure_setup_token(
                    session,
                    get_redis_client(settings),
                    ttl_seconds=settings.setup_token_ttl_seconds,
                )
                populated = token is not None and await _instance_has_orgs(session)
        if token:
            if populated:
                # Adminless-recovery on an instance that already has data is a
                # distinct, alarming event — not a routine fresh install.
                logger.error(
                    "RECOVERY MODE: this instance has existing organizations but no "
                    "active site admin. A new setup token was issued — if you did not "
                    "expect this, treat it as a security incident."
                )
            ttl = settings.setup_token_ttl_seconds
            validity = f"{ttl // 3600}h" if ttl >= 3600 else f"{max(1, ttl // 60)}m"
            border = "=" * 72
            logger.warning(
                "\n%s\n"
                "  FIRST-RUN SETUP: no site admin exists yet.\n"
                "  One-time setup token (valid %s, single use):\n\n"
                "      %s\n\n"
                "  Sign in at the UI and open /setup to claim global admin.\n"
                "%s",
                border,
                validity,
                token,
                border,
            )
    except Exception:
        logger.exception("Setup-token bootstrap check failed (continuing startup)")


async def _instance_has_orgs(session: AsyncSession) -> bool:
    result = await session.execute(text("SELECT EXISTS (SELECT 1 FROM orgs)"))
    return bool(result.scalar_one())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    logger.info("Starting Red Arch Knowledge Manager API")
    await _announce_setup_token_if_needed()

    yield

    logger.info("Shutting down Red Arch Knowledge Manager API")
    await close_redis_client()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    engine = get_engine(settings)

    app = FastAPI(
        title="Red Arch Knowledge Management API",
        version="2.0.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # CORS: we send credentials (cookies / Authorization), so the browser
    # forbids a wildcard origin in that mode and Starlette silently drops the
    # ACAO header — fail loud instead. Also enumerate the methods/headers the API
    # actually uses rather than "*", so a credentialed wildcard can never slip in.
    if "*" in settings.cors_origins:
        raise ValueError(
            "cors_origins must be an explicit allow-list, never '*', because "
            "allow_credentials=True is incompatible with a wildcard origin."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Org-ID",
            "X-Request-ID",
            "X-Test-User",
            "X-Test-Secret",
            "X-Internal-API-Key",
        ],
    )
    app.add_middleware(RequestLoggingMiddleware)

    # ServerErrorMiddleware sits above CORSMiddleware, so an unhandled 500 would
    # otherwise reach the browser without CORS headers (surfacing as a bare
    # "Network Error"). Re-attach them here so cross-origin callers see the 500.
    app.add_exception_handler(
        Exception, make_unhandled_exception_handler(settings.cors_origins)
    )

    # Observability must be wired here (before startup). Starlette forbids
    # adding middleware once the app enters the lifespan context, and the
    # Prometheus instrumentator installs middleware under the hood.
    setup_observability(
        app,
        engine,
        service_name="red-arch-km-api",
        log_level=settings.log_level,
    )

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
    app.include_router(attributes.router, prefix="/api/attributes", tags=["attributes"])
    app.include_router(
        entity_definitions.router, prefix="/api/entity-definitions", tags=["custom-entities"]
    )
    app.include_router(entity_records.router, prefix="/api/entities", tags=["custom-entities"])
    app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"])
    app.include_router(forms.router, prefix="/api/forms", tags=["forms"])
    app.include_router(views.router, prefix="/api/views", tags=["views"])
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
    # Public, unauthenticated form rendering + submission (org resolved from token).
    app.include_router(forms.public_router, prefix="/api/public/forms", tags=["forms-public"])
    # Public, token-authenticated inbound webhooks that start a workflow run.
    app.include_router(inbound.router, prefix="/api/inbound", tags=["inbound"])
    app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
    app.include_router(internal.router, prefix="/api/internal", tags=["internal"])
    app.include_router(setup.router, prefix="/api/setup", tags=["setup"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(migration.router, prefix="/api/migration", tags=["migration"])

    return app


app = create_app()
