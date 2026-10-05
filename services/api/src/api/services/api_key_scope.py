"""What a scoped API key may read: masks from its own assignments, folders that narrow.

An org API key is org-wide by default (``access_mode = 'org'``): its *operations*
are gated by scopes, its *data visibility* is the whole org. A key minted with any
dimension or folder assignment is ``'scoped'`` and reads with :class:`KeyScope`:

* **masks** — built from the key's own regions, roles, groups and departments by
  :func:`~api.services.permission_config.calculate_masks_from_assignments`, the
  same builder a member's masks come from. A key holding only a role reads with
  exactly the masks of a member holding only that role. A key with folders but no
  dimensions reads with the masks of a member with no assignments — never
  org-wide.
* **folder_ids** — ``None`` when the key lists no folders. Otherwise the listed
  folders plus every subfolder, found by walking ``parent_id`` from the listed ids
  (:meth:`~api.repositories.folder.FolderRepository.visible_subtrees`) — never by
  ``dot_path``, which is built from names that two root folders may share —
  intersected with the folders the masks may see. Folders only ever narrow; the
  masks still cap everything. Moving a folder under a listed one widens the key's
  folder set accordingly.

Resolved on every request (``require_api_key``) and on every tool call of a run the
key started (``tools/key_scope.py``), so a change to the key's assignments, a
folder's permissions or the tree applies immediately.

Fail closed: a scoped key with no assignment rows at all, an assignment that no
longer resolves inside the key's org, or masks past ``MAX_ACCESS_KEYS``
(:class:`~api.services.permission_config.TooManyAccessMasks`, raised as-is) is
refused. ``None`` masks never travel for a scoped key — downstream that means "no
filter" — and an empty list never does either: it is refused here, before it could.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, String, cast, literal, null, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from api.models.api_key import (
    ACCESS_MODE_SCOPED,
    ApiKey,
    api_key_departments,
    api_key_folders,
    api_key_groups,
    api_key_regions,
    api_key_roles,
)
from api.models.document import Folder
from api.models.org import Department, Group, Org, Region, Role
from api.repositories.folder import FolderRepository
from api.services.permission_config import calculate_masks_from_assignments
from api.services.search_access import with_unrestricted

_DIMENSIONS: tuple[tuple[str, Any, str, Any], ...] = (
    ("region", api_key_regions, "region_id", Region),
    ("role", api_key_roles, "role_id", Role),
    ("group", api_key_groups, "group_id", Group),
    ("department", api_key_departments, "department_id", Department),
)


class KeyScopeError(Exception):
    """A scoped key cannot be resolved to a safe scope; refuse it.

    The message is for operators (logs); callers never return it to the key holder.
    """


@dataclass(frozen=True, slots=True)
class KeyScope:
    """What a scoped key reads with. ``masks`` is never empty."""

    masks: tuple[int, ...]
    # None = no folder limit; otherwise the expanded, visibility-capped folder set
    # (may be empty: the key then sees no folder at all).
    folder_ids: frozenset[uuid.UUID] | None


def is_scoped(api_key: ApiKey) -> bool:
    return api_key.access_mode == ACCESS_MODE_SCOPED


def _assignments_query(api_key_id: uuid.UUID) -> Select[Any]:
    """Every assignment row of one key, with what it points at, in ONE statement.

    Outer joins, so a row whose target is missing (or hidden by RLS because it is in
    another org) still comes back — with a NULL org — and is refused rather than
    silently dropped (dropping a dimension would change the masks).
    """
    parts: list[Select[Any]] = []
    for kind, table, column, model in _DIMENSIONS:
        item = table.c[column]
        parts.append(
            select(
                literal(kind, String).label("kind"),
                item.label("item_id"),
                cast(model.permission_number, Integer).label("permission_number"),
                model.org_id.label("org_id"),
            )
            .select_from(table.outerjoin(model, model.id == item))
            .where(table.c.api_key_id == api_key_id)
        )
    parts.append(
        select(
            literal("folder", String).label("kind"),
            api_key_folders.c.folder_id.label("item_id"),
            cast(null(), Integer).label("permission_number"),
            Folder.org_id.label("org_id"),
        )
        .select_from(api_key_folders.outerjoin(Folder, Folder.id == api_key_folders.c.folder_id))
        .where(api_key_folders.c.api_key_id == api_key_id)
    )
    return union_all(*parts)  # type: ignore[return-value]


async def resolve_key_scope(session: AsyncSession, api_key: ApiKey) -> KeyScope:
    """The masks and folder set a scoped key reads with, resolved now.

    Raises :class:`KeyScopeError` (refuse) or ``TooManyAccessMasks`` (refuse, 422).
    Only meaningful for a scoped key; an org-wide key has no scope to resolve.
    """
    if not is_scoped(api_key):
        raise KeyScopeError(f"API key {api_key.id} is not scoped")
    rows = (await session.execute(_assignments_query(api_key.id))).all()
    if not rows:
        raise KeyScopeError(f"Scoped API key {api_key.id} has no assignments")
    if any(row.org_id != api_key.org_id for row in rows):
        raise KeyScopeError(f"Scoped API key {api_key.id} holds an assignment outside its org")

    org_number = (
        await session.execute(select(Org.permission_number).where(Org.id == api_key.org_id))
    ).scalar_one_or_none()
    if org_number is None:
        raise KeyScopeError(f"Org of API key {api_key.id} not found")

    numbers: dict[str, list[int]] = {kind: [] for kind, *_ in _DIMENSIONS}
    listed_folders: list[uuid.UUID] = []
    for row in rows:
        if row.kind == "folder":
            listed_folders.append(row.item_id)
        else:
            numbers[row.kind].append(row.permission_number)

    masks = with_unrestricted(
        calculate_masks_from_assignments(
            org_number,
            regions=numbers["region"],
            departments=numbers["department"],
            roles=numbers["role"],
            groups=numbers["group"],
        )
    )
    if not masks:  # cannot happen (assignments never yield []); never let it travel
        raise KeyScopeError(f"Scoped API key {api_key.id} resolved to no masks")

    folder_ids: frozenset[uuid.UUID] | None = None
    if listed_folders:
        expanded = await FolderRepository(session, api_key.org_id).visible_subtrees(listed_folders, masks)
        folder_ids = frozenset(fid for fid, visible in expanded.items() if visible)
    return KeyScope(masks=tuple(masks), folder_ids=folder_ids)
