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
* **A scoped API key** (bound to dimension assignments, optionally narrowed to
  folders) → :func:`api_key_access_keys` returns the masks of its own assignments,
  and :func:`api_key_folder_scope` the folders it may read, both resolved by
  ``require_api_key`` on every request (``api.services.api_key_scope``). Its masks
  are never ``None`` and never empty.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth.dependencies import OrgContext
from api.models.org import Org
from api.models.user import UserOrgMembership, UserProfile
from api.services.index_tags import folder_tag
from api.services.permission_config import calculate_user_masks_from_membership

if TYPE_CHECKING:
    from api.auth.api_key import ApiKeyPrincipal

# The key ingest writes for a document with no viewer configuration — "public
# within the org" (``FolderRepository.effective_view_masks`` returns an empty list,
# which the vector store records as this sentinel).
#
# Retrieval filters with MatchAny over the document's stored keys, so a mask list
# that omits this matches *no* unrestricted document. Every mask list handed to a
# search must therefore carry it, or a restricted member sees an empty knowledge
# base rather than a restricted one.
UNRESTRICTED_MASK = 0


def with_unrestricted(masks: list[int]) -> list[int]:
    """A user's own masks plus the public sentinel, de-duplicated, order stable."""
    return [UNRESTRICTED_MASK, *(m for m in masks if m != UNRESTRICTED_MASK)]


async def resolve_user_access_keys(session: AsyncSession, ctx: OrgContext) -> list[int] | None:
    """Return the user's access masks, or ``None`` for admins (unrestricted)."""
    if ctx.is_org_admin:
        return None
    org = await session.get(Org, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    return with_unrestricted(calculate_user_masks_from_membership(ctx.membership, org.permission_number))


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
    return with_unrestricted(calculate_user_masks_from_membership(membership, org.permission_number))


def service_key_access_keys() -> list[int] | None:
    """Access masks for an org service key: ``None`` (org-wide access)."""
    return None


def api_key_access_keys(principal: ApiKeyPrincipal) -> list[int] | None:
    """Masks for whoever holds an API key: org-wide (``None``) for an org key, the
    key's own assignment masks for a scoped key.

    A scoped key with no masks cannot happen through ``require_api_key``; if it
    ever does, refuse with a clear 403 rather than send brain-api an empty list
    (which it answers with nothing — ``None`` is the only "no filter").
    """
    if not principal.is_scoped:
        return service_key_access_keys()
    if not principal.masks:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key has no knowledge access")
    return list(principal.masks)


# brain-api ORs folder tags into one filter; past this the request is refused
# rather than truncated (truncating would silently search fewer folders).
MAX_FOLDER_TAGS = 1024


def api_key_folder_scope(
    principal: ApiKeyPrincipal,
    requested_folder_ids: list[uuid.UUID] | None,
) -> list[uuid.UUID] | None:
    """The folders a request may search, given the key's folder limits.

    * A key without folder limits: the requested folders, or ``None`` (no folder
      filter) when none were requested — today's behaviour.
    * A folder-limited key: every requested folder must be in its allowed set, else
      ``404 folder not found`` (a folder it may not read is indistinguishable from a
      missing one); none requested means the whole allowed set.

    An **empty list** comes back only for a folder-limited key whose allowed set is
    empty. Callers answer with no results without calling brain-api (which would
    answer an explicit empty ``folder_tags`` with nothing anyway).

    More than :data:`MAX_FOLDER_TAGS` folders is a ``422``.
    """
    requested = list(dict.fromkeys(requested_folder_ids or ()))
    allowed = principal.folder_ids
    if allowed is None:
        scoped = requested or None
    elif requested:
        if not set(requested) <= allowed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="folder not found")
        scoped = requested
    else:
        scoped = sorted(allowed, key=str)
    if scoped is not None and len(scoped) > MAX_FOLDER_TAGS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Too many folders to search at once ({len(scoped)}; the limit is {MAX_FOLDER_TAGS})",
        )
    return scoped


def folder_tags(folder_ids: list[uuid.UUID] | None) -> list[str] | None:
    """Translate REQUESTED folder ids into the synthetic ``folder:<id>`` tags used
    at ingest.

    Returned as an OR-filter (any of these) so a chat/search can be scoped to a
    set of folders. ``None`` (no folder filter) when none were requested — a
    request body's empty ``folder_ids`` means "not limited", not "no folders".
    A computed folder scope that came out EMPTY must be short-circuited by the
    caller (see :func:`api_key_folder_scope`); brain-api answers an explicit
    ``[]`` with nothing.
    """
    return [folder_tag(fid) for fid in folder_ids or ()] or None
