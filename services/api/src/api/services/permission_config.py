"""Convert permission configurations (list of dimension-name dicts) to access masks.

A permission config is a list of dicts like:
    [
        {"region": "North", "role": "Manager"},
        {"department": "Legal"},
    ]

Each entry represents ONE permission group — a user must match all dimensions
in a single entry to satisfy it. Multiple entries are OR'd together.

This service resolves names to permission_number values from the org's
dimension tables, then encodes them into access masks.
"""

from __future__ import annotations

import logging
import uuid
from itertools import product as cartesian_product

from access_mask import MAX_DEPT, MAX_GROUP, MAX_REGION, MAX_ROLE, encode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.org import Department, Group, Org, Region, Role
from api.models.user import UserOrgMembership

logger = logging.getLogger(__name__)


async def _resolve_dimension_number(
    session: AsyncSession,
    model: type[Region | Department | Role | Group],
    org_id: uuid.UUID,
    name: str,
) -> int | None:
    result = await session.execute(select(model.permission_number).where(model.org_id == org_id, model.name == name))
    return result.scalar_one_or_none()


async def permission_config_to_masks(
    session: AsyncSession,
    org_id: uuid.UUID,
    config: list[dict[str, str]] | None,
) -> list[int]:
    """Convert a permission config to a list of access mask integers.

    Each config entry becomes one mask (unmatched dimensions use wildcard MAX).
    """
    if not config:
        return []

    org_result = await session.execute(select(Org.permission_number).where(Org.id == org_id))
    org_number = org_result.scalar_one_or_none()
    if org_number is None:
        logger.warning("Org %s has no permission_number", org_id)
        return []

    masks: list[int] = []
    for entry in config:
        region = (
            await _resolve_dimension_number(session, Region, org_id, entry["region"])
            if "region" in entry
            else MAX_REGION
        )
        dept = (
            await _resolve_dimension_number(session, Department, org_id, entry["department"])
            if "department" in entry
            else MAX_DEPT
        )
        role = await _resolve_dimension_number(session, Role, org_id, entry["role"]) if "role" in entry else MAX_ROLE
        group = (
            await _resolve_dimension_number(session, Group, org_id, entry["group"]) if "group" in entry else MAX_GROUP
        )

        if region is None or dept is None or role is None or group is None:
            logger.warning("Unresolved dimension in permission config entry %s (org=%s)", entry, org_id)
            continue

        masks.append(encode(org=org_number, region=region, dept=dept, role=role, group=group))

    return masks


def calculate_user_masks_from_membership(
    membership: UserOrgMembership,
    org_number: int,
) -> list[int]:
    """Return all access masks a user can assert via their membership.

    Generates the Cartesian product of regions × departments × roles × groups
    so any matching document mask grants access.
    """
    region_numbers = [r.permission_number for r in membership.regions] or [0]
    dept_numbers = [d.permission_number for d in membership.departments] or [0]
    role_numbers = [r.permission_number for r in membership.roles] or [0]
    group_numbers = [g.permission_number for g in membership.groups] or [0]

    masks: list[int] = []
    for region, dept, role, group in cartesian_product(region_numbers, dept_numbers, role_numbers, group_numbers):
        masks.append(encode(org=org_number, region=region, dept=dept, role=role, group=group))
    return masks
