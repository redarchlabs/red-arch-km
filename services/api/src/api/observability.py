"""Observability wiring for the API service.

Centralises OTel auto-instrumentation + Prometheus /metrics setup so the
application entry point stays thin. Instrumentation is idempotent; calling
setup twice in tests is safe.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator
from shared_config import configure_logging, configure_telemetry
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_instrumented = False


def setup_observability(
    app: FastAPI,
    engine: AsyncEngine,
    *,
    service_name: str,
    log_level: str,
) -> None:
    """Configure logging, tracing, metrics, and auto-instrumentation.

    Instrumentation flags prevent double-registration when tests spin up
    multiple app instances in the same process.
    """
    global _instrumented

    configure_logging(level=log_level)
    configure_telemetry(service_name=service_name)

    if _instrumented:
        # Library-level instrumentors blow up if called twice. In production
        # this branch is never hit; it's a safety net for the test suite.
        Instrumentator().expose(app, endpoint="/metrics")
        FastAPIInstrumentor.instrument_app(app)
        return

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/healthz,/readyz,/metrics")
    HTTPXClientInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/healthz", "/readyz", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    _instrumented = True
    logger.info("Observability configured for %s", service_name)
