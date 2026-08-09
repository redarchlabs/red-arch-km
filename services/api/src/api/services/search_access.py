"""Permission-mask helpers for knowledge-base search/chat.

The security of search + RAG chat lives here, not in ``BrainAPIClient``: callers
must translate the requester's membership into the ``access_keys`` masks that the
brain-api uses to filter retrievable content, or they leak cross-permission data.

Three requester shapes exist:

* **A user** (Clerk session) → :func:`resolve_user_access_keys` derives masks from
  their membership (``None`` only for org admins = unrestricted).
* **An agent run acting for a user** → :func:`resolve_profile_access_keys` derives
  the same masks from a bare profile id, because a tool handler has an
  ``actor_user_id`` rather than a request's ``OrgContext``.
* **An org service API key** → :func:`service_key_access_keys` returns ``None``
  (org-wide access). An org key is an org-level credential; its *operations* are
  gated by scopes, but its *data visibility* is org-wide. This is intentional and
  surfaced to admins at key-creation time.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth.dependencies import OrgContext
from api.models.org import Org
from api.models.user import UserOrgMembership, UserProfile
from api.services.permission_config import calculate_user_masks_from_membership

# The key ingest writes for a document with no viewer configuration — "public
# within the org" (``FolderRepository.effective_view_masks`` returns an empty list,
# which the vector store records as this sentinel).
#
# Retrieval filters with MatchAny over the document's stored keys, so a mask list
# that omits this matches *no* unrestricted document. Every mask list handed to a
# search must therefore carry it, or a restricted member sees an empty knowledge
# base rather than a restricted one.
UNRESTRICTED_MASK = 0


def _with_unrestricted(masks: list[int]) -> list[int]:
    """A user's own masks plus the public sentinel, de-duplicated, order stable."""
    return [UNRESTRICTED_MASK, *(m for m in masks if m != UNRESTRICTED_MASK)]


async def resolve_user_access_keys(session: AsyncSession, ctx: OrgContext) -> list[int] | None:
    """Return the user's access masks, or ``None`` for admins (unrestricted)."""
    if ctx.is_org_admin:
        return None
    org = await session.get(Org, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    return _with_unrestricted(calculate_user_masks_from_membership(ctx.membership, org.permission_number))


async def resolve_profile_access_keys(
    session: AsyncSession,
    org_id: uuid.UUID,
    profile_id: uuid.UUID,
) -> list[int] | None:
    """Masks for a profile in an org — the agent-run counterpart of the user path.

    Returns ``None`` (unrestricted) for an org admin or a site admin, mirroring
    :func:`resolve_user_access_keys` exactly; a profile with no membership in the
    org gets ``[]``, which callers must treat as "no access" rather than as
    "unrestricted" (an empty list means something different downstream).

    The membership's dimension collections are eager-loaded: they are lazy
    many-to-many relationships, and touching them after the fact on an async
    session raises ``MissingGreenlet``.
    """
    membership = (
        await session.execute(
            select(UserOrgMembership)
            .where(UserOrgMembership.profile_id == profile_id, UserOrgMembership.org_id == org_id)
            .options(
                selectinload(UserOrgMembership.regions),
                selectinload(UserOrgMembership.departments),
                selectinload(UserOrgMembership.roles),
                selectinload(UserOrgMembership.groups),
            )
        )
    ).scalar_one_or_none()

    # A site admin has org-wide reach without needing a membership row — the same
    # elevation require_org_access grants when it synthesises one.
    profile = await session.get(UserProfile, profile_id)
    if profile is not None and profile.is_site_admin:
        return None
    if membership is None:
        return []
    if membership.is_org_admin:
        return None

    org = await session.get(Org, org_id)
    if org is None:
        return []
    return _with_unrestricted(calculate_user_masks_from_membership(membership, org.permission_number))


def service_key_access_keys() -> list[int] | None:
    """Access masks for an org service key: ``None`` (org-wide access)."""
    return None


def folder_tags(folder_ids: list[uuid.UUID]) -> list[str] | None:
    """Translate folder ids into the synthetic ``folder:<id>`` tags used at ingest.

    Returned as an OR-filter (any of these) so a chat/search can be scoped to a
    set of folders. ``None`` when no folder scope was requested.
    """
    return [f"folder:{fid}" for fid in folder_ids] or None
