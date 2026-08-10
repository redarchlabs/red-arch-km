"""Database engine and session factory (module-level singletons)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from api.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _json_default(obj: Any) -> Any:
    """Make JSONB columns tolerate the value types that flow through them —
    notably ``Decimal`` (numeric entity fields, e.g. a calculated total),
    ``datetime``/``date``, and ``UUID`` (every record's own id). Without this,
    capturing a numeric record value into ``workflow_outbox.after_data`` raises
    'Decimal is not JSON serializable'.

    ``UUID`` is here because an agent's ``list_records`` result carries each row's
    id, and the run loop writes every tool result into ``agent_run_steps.content``
    (JSONB). So the first time an agent listed records that actually existed, the
    step insert raised and the sweep finalised the whole run as 'execution failed'
    — with no steps saved, because the failing insert *was* the step. The tool
    worked; persisting the evidence of it did not.
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def json_serializer(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


def get_engine(settings: Settings) -> AsyncEngine:
    """The process-wide engine.

    Pool sizing is a budget against PostgreSQL's ``max_connections`` (100 by
    default, less ``superuser_reserved_connections``), shared by every process
    that connects. The API is the only heavy consumer today — the Celery worker
    reaches the database through the API's internal endpoints rather than
    directly — so the default ceiling of ``pool_size + max_overflow`` leaves the
    server most of its headroom while giving a single API process room for
    long-lived streams alongside ordinary request traffic.

    Configurable because the right number is a deployment fact, not a code fact:
    it depends on how many API replicas share one server. When replicas × this
    ceiling approaches ``max_connections``, the answer is a connection pooler
    (PgBouncer) rather than a smaller pool — and this schema is already suited to
    one, since all tenant scoping is ``SET LOCAL`` (see :mod:`api.db_scope`) and
    so survives transaction-level pooling.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            # Reclaim a connection held by a stalled caller rather than blocking
            # new work forever; surfaces as a clear TimeoutError in the logs.
            pool_timeout=settings.db_pool_timeout_seconds,
            echo=settings.debug,
            pool_pre_ping=True,
            json_serializer=json_serializer,
        )
    return _engine


def get_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(settings),
            expire_on_commit=False,
        )
    return _session_factory


async def dispose_engine() -> None:
    """Dispose engine on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
