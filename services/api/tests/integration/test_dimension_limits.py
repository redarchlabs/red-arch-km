"""A dimension may never be numbered at its wildcard value (real PostgreSQL).

Masks use a dimension's MAX (region/role 31, group 127, dept 15) as "any value", so
a region numbered 31 would make a folder scoped to it visible to every region.
Creation therefore stops at MAX-1 with a clear error.
"""

from __future__ import annotations

import uuid

import pytest
from access_mask import MAX_DEPT, MAX_GROUP, MAX_REGION, MAX_ROLE
from api.models.org import Department, Group, Org, Region, Role
from api.repositories.dimension import DimensionLimitReached, DimensionRepository
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("model", "wildcard"), [(Region, MAX_REGION), (Role, MAX_ROLE), (Group, MAX_GROUP), (Department, MAX_DEPT)]
)
async def test_the_last_number_before_the_wildcard_is_the_limit(
    admin_session: AsyncSession, model: type, wildcard: int
) -> None:
    org = Org(name=f"DL-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    # Pre-fill up to MAX-2 so the next create gets MAX-1.
    admin_session.add_all([model(name=f"d{i}", permission_number=i, org_id=org.id) for i in range(1, wildcard - 1)])
    await admin_session.flush()
    repo = DimensionRepository(admin_session, model, org.id)

    last = await repo.create(name="last")
    assert last.permission_number == wildcard - 1

    with pytest.raises(DimensionLimitReached, match=str(wildcard - 1)):
        await repo.create(name="one too many")
