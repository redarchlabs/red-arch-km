"""Seed the database with fixtures for E2E tests.

Creates one org, a handful of permission dimensions, a test admin user,
a couple of folders, and a set of tags. The E2E suite then logs in as
the test admin via the X-Test-User header (see dependencies.py).

Usage:
    DATABASE_URL=postgresql+asyncpg://… python -m api.scripts.seed_e2e

Idempotent — safe to run multiple times. Records are upserted by name.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid

from sqlalchemy import select, text

from api.config import get_settings
from api.db import get_session_factory
from api.models.document import Folder, Tag
from api.models.org import Department, Group, Org, Region, Role
from api.models.user import UserOrgMembership, UserProfile

logger = logging.getLogger("seed")

E2E_ORG_NAME = "E2E Test Org"
E2E_ADMIN_SUB = "e2e-e2e_admin"
E2E_ADMIN_USERNAME = "e2e_admin"
E2E_ADMIN_EMAIL = "e2e_admin@e2e.local"

REGIONS = ["North", "South"]
DEPARTMENTS = ["Engineering", "Legal"]
ROLES = ["Manager", "Contributor"]
GROUPS = ["Alpha", "Beta"]
TAGS = ["onboarding", "policy", "finance"]


async def _get_or_create(session, model, *, filters, defaults):  # type: ignore[no-untyped-def]
    result = await session.execute(
        select(model).filter_by(**filters)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    instance = model(**filters, **defaults)
    session.add(instance)
    await session.flush()
    return instance


async def seed() -> None:
    settings = get_settings()
    factory = get_session_factory(settings)

    async with factory() as session:
        # Orgs are not RLS-scoped on create (seed script runs as admin/bypass role)
        org = await _get_or_create(
            session, Org,
            filters={"name": E2E_ORG_NAME},
            defaults={"permission_number": 1, "description": "Created by seed_e2e"},
        )
        org_id: uuid.UUID = org.id

        # Site admin user
        admin = await _get_or_create(
            session, UserProfile,
            filters={"keycloak_sub": E2E_ADMIN_SUB},
            defaults={
                "username": E2E_ADMIN_USERNAME,
                "email": E2E_ADMIN_EMAIL,
                "is_site_admin": True,
            },
        )

        # Set tenant context for the remaining inserts (RLS)
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_id)},
        )

        # Permission dimensions
        created_regions = []
        for i, name in enumerate(REGIONS, start=1):
            created_regions.append(
                await _get_or_create(
                    session, Region,
                    filters={"org_id": org_id, "name": name},
                    defaults={"permission_number": i},
                )
            )
        for i, name in enumerate(DEPARTMENTS, start=1):
            await _get_or_create(
                session, Department,
                filters={"org_id": org_id, "name": name},
                defaults={"permission_number": i},
            )
        for i, name in enumerate(ROLES, start=1):
            await _get_or_create(
                session, Role,
                filters={"org_id": org_id, "name": name},
                defaults={"permission_number": i},
            )
        for i, name in enumerate(GROUPS, start=1):
            await _get_or_create(
                session, Group,
                filters={"org_id": org_id, "name": name},
                defaults={"permission_number": i},
            )

        # Membership
        membership = await _get_or_create(
            session, UserOrgMembership,
            filters={"profile_id": admin.id, "org_id": org_id},
            defaults={"is_org_admin": True},
        )
        # Link admin to first region so permission mask is non-zero for chain tests
        if not membership.regions:
            membership.regions = [created_regions[0]]

        # Folders
        await _get_or_create(
            session, Folder,
            filters={"org_id": org_id, "name": "Public", "parent_id": None},
            defaults={"dot_path": "Public", "view_permission_masks": []},
        )
        await _get_or_create(
            session, Folder,
            filters={"org_id": org_id, "name": "Engineering", "parent_id": None},
            defaults={"dot_path": "Engineering", "view_permission_masks": []},
        )

        # Tags
        for name in TAGS:
            await _get_or_create(
                session, Tag,
                filters={"org_id": org_id, "name": name},
                defaults={},
            )

        await session.commit()

    logger.info("Seed complete — org_id=%s admin=%s", org_id, E2E_ADMIN_USERNAME)
    print(f"SEEDED_ORG_ID={org_id}")  # machine-readable for test harnesses  # noqa: T201


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(seed())
    except Exception as e:
        logger.error("Seed failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
