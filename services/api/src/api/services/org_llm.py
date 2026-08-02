"""Org-level default LLM model lookup.

One org can be pinned to local inference and another to a 3rd-party provider:
``orgs.default_llm_model`` holds a model id that ``OPENAI_MODEL_ROUTES`` maps to
its serving endpoint. This helper is the single place request paths (chat,
search) resolve that pin; the workflow engine has session-bound twins on its
executor classes (``ActionExecutor._org_default_model``).

Resolution order everywhere: explicit per-call model > org pin > env defaults.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.org import Org


async def org_default_llm_model(session: AsyncSession, org_id: uuid.UUID | str) -> str | None:
    """The org's pinned LLM model id, or None for the platform default."""
    oid = uuid.UUID(str(org_id))
    org = await session.get(Org, oid)
    return getattr(org, "default_llm_model", None) if org else None
