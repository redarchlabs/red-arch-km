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
    # Backstop so a runaway extraction (e.g. a pathological PDF) can't hang a
    # worker slot forever. The soft limit raises SoftTimeLimitExceeded inside the
    # task (caught by task_extract_and_ingest to report FAILED); the hard limit
    # SIGKILLs the child as a last resort. Generous enough not to trip a
    # legitimately slow brain-api ingest + its bounded in-process retries.
    task_soft_time_limit=int(os.environ.get("TASK_SOFT_TIME_LIMIT", "1740")),
    task_time_limit=int(os.environ.get("TASK_TIME_LIMIT", "1800")),
)

app.autodiscover_tasks(["worker.tasks"])

# Workflow engine cadence: drain the outbox frequently, keep partitions ahead.
app.conf.beat_schedule = {
    "workflow-sweep-outbox": {
        "task": "worker.tasks.workflow.sweep_outbox",
        "schedule": float(os.environ.get("WORKFLOW_SWEEP_INTERVAL", "10")),
        "options": {"expires": 30},
    },
    "workflow-maintain-partitions": {
        "task": "worker.tasks.workflow.maintain_partitions",
        # Daily is plenty to keep 1-2 months of partitions pre-created.
        "schedule": float(os.environ.get("WORKFLOW_PARTITION_INTERVAL", "86400")),
    },
    # Liveness beacon for the site-admin console: proves beat -> broker -> worker
    # is healthy. A stale/absent heartbeat there means beat is down.
    "beat-heartbeat": {
        "task": "worker.tasks.monitoring.beat_heartbeat",
        "schedule": float(os.environ.get("BEAT_HEARTBEAT_INTERVAL", "15")),
        "options": {"expires": 30},
    },
}


@worker_process_init.connect(weak=False)  # type: ignore[untyped-decorator]  # celery signals are untyped
def _init_observability(**_kwargs: object) -> None:
    """Configure logging + telemetry in each worker process.

    `worker_process_init` fires after each child fork, so OTel exporters
    and logging handlers are initialised per-process rather than inherited
    from the pre-fork parent (which would drop connections).
    """
    configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    configure_telemetry(service_name="red-arch-km-worker")
    CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]  # otel celery instrumentor is untyped
    HTTPXClientInstrumentor().instrument()
    logger.info("Worker observability configured")
