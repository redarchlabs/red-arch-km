"""Permission-mask helpers for knowledge-base search/chat.

The security of search + RAG chat lives here, not in ``BrainAPIClient``: callers
must translate the requester's membership into the ``access_keys`` masks that the
brain-api uses to filter retrievable content, or they leak cross-permission data.

Two requester shapes exist:

* **A user** (Clerk session) → :func:`resolve_user_access_keys` derives masks from
  their membership (``None`` only for org admins = unrestricted).
* **An org service API key** → :func:`service_key_access_keys` returns ``None``
  (org-wide access). An org key is an org-level credential; its *operations* are
  gated by scopes, but its *data visibility* is org-wide. This is intentional and
  surfaced to admins at key-creation time.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext
from api.models.org import Org
from api.services.permission_config import calculate_user_masks_from_membership


async def resolve_user_access_keys(session: AsyncSession, ctx: OrgContext) -> list[int] | None:
    """Return the user's access masks, or ``None`` for admins (unrestricted)."""
    if ctx.is_org_admin:
        return None
    org = await session.get(Org, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    return calculate_user_masks_from_membership(ctx.membership, org.permission_number)


def service_key_access_keys() -> list[int] | None:
    """Access masks for an org service key: ``None`` (org-wide access)."""
    return None


def folder_tags(folder_ids: list[uuid.UUID]) -> list[str] | None:
    """Translate folder ids into the synthetic ``folder:<id>`` tags used at ingest.

    Returned as an OR-filter (any of these) so a chat/search can be scoped to a
    set of folders. ``None`` when no folder scope was requested.
    """
    return [f"folder:{fid}" for fid in folder_ids] or None
