"""Celery application for the worker service.

Hooks OpenTelemetry Celery instrumentation at process startup so each task
invocation produces a span; HTTPX instrumentation traces brain-api calls.
The worker-process signal ensures instrumentation runs in forked child
processes, not just the master.
"""

import logging
import os

from celery import Celery
from celery.signals import worker_process_init
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from shared_config import configure_logging, configure_telemetry

logger = logging.getLogger(__name__)

app = Celery(
    "redarch_km_worker",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=30,
    task_max_retries=3,
)

app.autodiscover_tasks(["worker.tasks"])


@worker_process_init.connect(weak=False)
def _init_observability(**_kwargs: object) -> None:
    """Configure logging + telemetry in each worker process.

    `worker_process_init` fires after each child fork, so OTel exporters
    and logging handlers are initialised per-process rather than inherited
    from the pre-fork parent (which would drop connections).
    """
    configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    configure_telemetry(service_name="red-arch-km-worker")
    CeleryInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    logger.info("Worker observability configured")
