"""Structured JSON logging with automatic OpenTelemetry trace-id injection.

Use `configure_logging(level)` at process startup. The resulting handler
emits JSON objects with `trace_id` and `span_id` populated from the active
OTel context so logs can be correlated with traces across services.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON with trace context."""

    # Fields on LogRecord that are always present; we copy everything else
    # from the record's __dict__ into the extra payload.
    _STANDARD_ATTRS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
            "message",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        span = trace.get_current_span()
        span_context = span.get_span_context() if span else None
        if span_context and span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")

        for key, value in record.__dict__.items():
            if key in self._STANDARD_ATTRS or key.startswith("_"):
                continue
            payload[key] = _safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _safe(value: Any) -> Any:
    """Reduce unserialisable values to strings so logging never fails."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter as the root handler.

    Idempotent: replaces any existing handlers on the root logger so
    subsequent library imports don't duplicate log output.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers = [handler]

    # Uvicorn sets up its own loggers with propagate=False — we want those
    # to flow through our root handler instead.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
