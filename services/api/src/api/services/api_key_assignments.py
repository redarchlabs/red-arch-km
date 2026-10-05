"""A scoped API key's assignments: validate at mint, save, label, guard deletes.

The five join tables (``api_key_regions`` / ``_roles`` / ``_groups`` /
``_departments`` / ``_folders``) carry no ``org_id`` and therefore no RLS. So every
id is checked here against the key's org — on the admin's tenant session AND with
an explicit ``org_id`` filter — before a row is written. Assignments are set once,
at mint; there is no update path (revoke and re-mint instead).

Deleting a role, region, group, department or folder that a key still holds is
refused by the database (``ON DELETE NO ACTION``, deferred to commit); the API
turns that into a 409 naming the active keys (:func:`active_keys_holding`). The
delete path first locks the item (``FOR UPDATE``) and purges the rows of revoked
and expired keys (:func:`purge_inactive_holders`, an explicit step — the check
itself changes nothing). Minting takes ``FOR SHARE`` on every row it validates, so
a mint and a delete of the same item serialise instead of racing past each other.
Both then check the deferred constraints in-handler (:func:`check_constraints_now`)
so a violation is a 409 there, not a failed commit after the response.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Table, delete, literal, or_, select, text, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.api_key import (
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
from api.services.permission_config import TooManyAccessMasks, calculate_masks_from_assignments
from api.services.search_access import MAX_FOLDER_TAGS, with_unrestricted

# kind → (join table, item column, target model). Kinds match the /dimensions
# route segment, plus "folders".
ASSIGNMENT_TABLES: dict[str, tuple[Table, str, Any]] = {
    "regions": (api_key_regions, "region_id", Region),
    "roles": (api_key_roles, "role_id", Role),
    "groups": (api_key_groups, "group_id", Group),
    "departments": (api_key_departments, "department_id", Department),
    "folders": (api_key_folders, "folder_id", Folder),
}
_DIMENSION_KINDS = ("regions", "roles", "groups", "departments")
_SINGULAR = {"regions": "region", "roles": "role", "groups": "group", "departments": "department", "folders": "folder"}


class ApiKeyAssignmentError(ValueError):
    """An assignment cannot be minted (unknown id, mask cap, hidden folder)."""


@dataclass(frozen=True, slots=True)
class KeyAssignments:
    """Ids per kind, de-duplicated, order kept."""

    regions: tuple[uuid.UUID, ...] = ()
    roles: tuple[uuid.UUID, ...] = ()
    groups: tuple[uuid.UUID, ...] = ()
    departments: tuple[uuid.UUID, ...] = ()
    folders: tuple[uuid.UUID, ...] = ()

    @classmethod
    def of(cls, **ids: list[uuid.UUID]) -> KeyAssignments:
        return cls(**{kind: tuple(dict.fromkeys(values)) for kind, values in ids.items()})

    def by_kind(self) -> dict[str, tuple[uuid.UUID, ...]]:
        return {kind: getattr(self, kind) for kind in ASSIGNMENT_TABLES}

    @property
    def is_empty(self) -> bool:
        return not any(self.by_kind().values())


@dataclass(slots=True)
class AssignmentLabels:
    """What the admin list shows for one key: ``(id, name)`` per kind."""

    regions: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    roles: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    groups: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    departments: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    folders: list[tuple[uuid.UUID, str]] = field(default_factory=list)


async def validate_assignments(session: AsyncSession, org_id: uuid.UUID, assignments: KeyAssignments) -> None:
    """Raise :class:`ApiKeyAssignmentError` unless every id is in ``org_id``, the
    dimensions stay under the mask cap, every listed folder is visible to the masks
    those dimensions produce, and the folders' expanded set fits a search.

    Every row read here is locked ``FOR SHARE`` until the mint commits, so a
    concurrent delete of one of them waits for the mint (and then sees the key)."""
    numbers: dict[str, list[int]] = {}
    for kind in _DIMENSION_KINDS:
        ids = getattr(assignments, kind)
        _table, _column, model = ASSIGNMENT_TABLES[kind]
        found: dict[uuid.UUID, int] = {}
        if ids:
            stmt = (
                select(model.id, model.permission_number)
                .where(model.org_id == org_id, model.id.in_(ids))
                .with_for_update(read=True)
            )
            found = dict((await session.execute(stmt)).tuples().all())
        _refuse_unknown(kind, ids, found.keys())
        numbers[kind] = [found[i] for i in ids]

    org_number = (await session.execute(select(Org.permission_number).where(Org.id == org_id))).scalar_one_or_none()
    if org_number is None:
        raise ApiKeyAssignmentError("Organization not found")
    try:
        masks = with_unrestricted(
            calculate_masks_from_assignments(
                org_number,
                regions=numbers["regions"],
                departments=numbers["departments"],
                roles=numbers["roles"],
                groups=numbers["groups"],
            )
        )
    except TooManyAccessMasks as exc:
        raise ApiKeyAssignmentError(str(exc)) from exc

    if assignments.folders:
        await _validate_folders(session, org_id, assignments.folders, masks)


async def _validate_folders(
    session: AsyncSession, org_id: uuid.UUID, folder_ids: tuple[uuid.UUID, ...], masks: list[int]
) -> None:
    paths = dict(
        (
            await session.execute(
                select(Folder.id, Folder.dot_path)
                .where(Folder.org_id == org_id, Folder.id.in_(folder_ids))
                .with_for_update(read=True)
            )
        )
        .tuples()
        .all()
    )
    _refuse_unknown("folders", folder_ids, paths.keys())
    expanded = await FolderRepository(session, org_id).visible_subtrees(folder_ids, masks)
    hidden = sorted(paths[i] for i in folder_ids if not expanded.get(i))
    if hidden:
        raise ApiKeyAssignmentError(
            "These folders are not visible to the key's region/role/group/department assignments, so the "
            f"key could never read them: {', '.join(hidden)}"
        )
    reach = sum(1 for visible in expanded.values() if visible)
    if reach > MAX_FOLDER_TAGS:
        raise ApiKeyAssignmentError(
            f"The chosen folders cover {reach} folders once their subfolders are included; the limit is "
            f"{MAX_FOLDER_TAGS}, because a search can be limited to at most that many. Choose fewer or "
            "narrower folders."
        )


def _refuse_unknown(kind: str, wanted: tuple[uuid.UUID, ...], found: Any) -> None:
    missing = [str(i) for i in wanted if i not in set(found)]
    if missing:
        # One message for "does not exist" and "belongs to another org".
        raise ApiKeyAssignmentError(f"Unknown {_SINGULAR[kind]} id(s) in this organization: {', '.join(missing)}")


async def save_assignments(session: AsyncSession, api_key_id: uuid.UUID, assignments: KeyAssignments) -> None:
    """Insert the (already validated) rows for a freshly created key."""
    for kind, ids in assignments.by_kind().items():
        if not ids:
            continue
        table, column, _model = ASSIGNMENT_TABLES[kind]
        await session.execute(table.insert(), [{"api_key_id": api_key_id, column: i} for i in ids])


async def load_assignment_labels(session: AsyncSession, key_ids: list[uuid.UUID]) -> dict[uuid.UUID, AssignmentLabels]:
    """Every key's assignments with names (folders by path), in one statement."""
    labels: dict[uuid.UUID, AssignmentLabels] = {k: AssignmentLabels() for k in key_ids}
    if not key_ids:
        return labels
    parts = []
    for kind, (table, column, model) in ASSIGNMENT_TABLES.items():
        name = model.dot_path if model is Folder else model.name
        parts.append(
            select(literal(kind).label("kind"), table.c.api_key_id, model.id, name.label("name"))
            .select_from(table.join(model, model.id == table.c[column]))
            .where(table.c.api_key_id.in_(key_ids))
        )
    rows = (await session.execute(union_all(*parts).order_by("kind", "name"))).all()
    for kind, key_id, item_id, name in rows:
        getattr(labels[key_id], kind).append((item_id, name))
    return labels


async def lock_item(session: AsyncSession, org_id: uuid.UUID, kind: str, item_id: uuid.UUID) -> bool:
    """Lock a dimension value or folder ``FOR UPDATE`` before deleting it. Waits for
    any mint holding it ``FOR SHARE``; False when it does not exist (any more)."""
    _table, _column, model = ASSIGNMENT_TABLES[kind]
    stmt = select(model.id).where(model.id == item_id, model.org_id == org_id).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def purge_inactive_holders(session: AsyncSession, org_id: uuid.UUID, kind: str, item_id: uuid.UUID) -> int:
    """Delete the assignment rows that revoked and expired keys hold on ``item_id``
    (they no longer matter), so they never block deleting it. Returns how many."""
    table, column, _model = ASSIGNMENT_TABLES[kind]
    inactive = select(ApiKey.id).where(
        ApiKey.org_id == org_id,
        or_(ApiKey.revoked_at.is_not(None), ApiKey.expires_at <= datetime.now(UTC)),
    )
    result = await session.execute(delete(table).where(table.c[column] == item_id, table.c.api_key_id.in_(inactive)))
    return int(getattr(result, "rowcount", 0) or 0)


async def active_keys_holding(session: AsyncSession, org_id: uuid.UUID, kind: str, item_id: uuid.UUID) -> list[str]:
    """Names of the org's ACTIVE keys holding ``item_id``. Read-only."""
    table, column, _model = ASSIGNMENT_TABLES[kind]
    now = datetime.now(UTC)
    rows = await session.execute(
        select(ApiKey.name)
        .join(table, table.c.api_key_id == ApiKey.id)
        .where(
            table.c[column] == item_id,
            ApiKey.org_id == org_id,
            ApiKey.revoked_at.is_(None),
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        )
        .order_by(ApiKey.name)
    )
    return list(rows.scalars().all())


async def check_constraints_now(session: AsyncSession) -> None:
    """Flush, then check every deferred constraint immediately.

    The assignment FKs are ``DEFERRABLE INITIALLY DEFERRED`` (so an org delete can
    cascade); left alone they are checked at commit — after the response, where a
    violation can only become a dropped connection. Checking here raises
    ``IntegrityError`` inside the handler instead.
    """
    await session.flush()
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def in_use_detail(kind: str, key_names: list[str]) -> str:
    if not key_names:
        return (
            f"This {_SINGULAR[kind]} was just assigned to an API key. Revoke that key (and re-issue it without "
            "it) before deleting it."
        )
    return (
        f"This {_SINGULAR[kind]} is assigned to active API key(s): {', '.join(key_names)}. "
        "Revoke those keys (and re-issue them without it) before deleting it."
    )
