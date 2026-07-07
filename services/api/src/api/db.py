"""Database engine and session factory (module-level singletons)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from api.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _json_default(obj: Any) -> Any:
    """Make JSONB columns tolerate the value types that flow through them —
    notably ``Decimal`` (numeric entity fields, e.g. a calculated total) and
    ``datetime``/``date``. Without this, capturing a numeric record value into
    ``workflow_outbox.after_data`` raises 'Decimal is not JSON serializable'."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def json_serializer(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


def get_engine(settings: Settings) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=10,
            max_overflow=5,
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
