"""Observability wiring for brain_api.

Provides OTel auto-instrumentation, Prometheus /metrics exposure, and
domain-specific metrics for the ingest pipeline + search latency.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator
from shared_config import configure_logging, configure_telemetry, get_meter

logger = logging.getLogger(__name__)

_instrumented = False


class BrainMetrics:
    """Application-level metrics registered once during lifespan startup."""

    def __init__(self) -> None:
        meter = get_meter("brain_api")
        self.chunks_ingested = meter.create_counter(
            "brain_chunks_ingested_total",
            description="Number of chunks stored in the vector index",
            unit="1",
        )
        self.triplets_ingested = meter.create_counter(
            "brain_triplets_ingested_total",
            description="Number of knowledge graph triplets stored",
            unit="1",
        )
        self.ingest_duration_ms = meter.create_histogram(
            "brain_ingest_duration_ms",
            description="Document ingestion duration in milliseconds",
            unit="ms",
        )
        self.search_duration_ms = meter.create_histogram(
            "brain_search_duration_ms",
            description="Vector search latency in milliseconds",
            unit="ms",
        )


_metrics: BrainMetrics | None = None


def get_metrics() -> BrainMetrics:
    """Return process-wide BrainMetrics, initialising on first call."""
    global _metrics
    if _metrics is None:
        _metrics = BrainMetrics()
    return _metrics


def setup_observability(
    app: FastAPI,
    *,
    service_name: str,
    log_level: str,
) -> None:
    global _instrumented

    configure_logging(level=log_level)
    configure_telemetry(service_name=service_name)
    get_metrics()

    if _instrumented:
        Instrumentator().expose(app, endpoint="/metrics")
        FastAPIInstrumentor.instrument_app(app)
        return

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/healthz,/metrics")
    HTTPXClientInstrumentor().instrument()

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/healthz", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    _instrumented = True
    logger.info("Observability configured for %s", service_name)
