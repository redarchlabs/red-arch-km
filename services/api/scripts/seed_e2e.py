"""Seed the database with fixtures for E2E tests.

Creates one org, a handful of permission dimensions, a test admin user,
a couple of folders, and a set of tags. The E2E suite then logs in as
the test admin via the X-Test-User header (see dependencies.py).

Usage (run from services/api):
    DATABASE_URL=postgresql+asyncpg://… python -m scripts.seed_e2e
    # or, from the repo root: make seed-e2e

Idempotent — safe to run multiple times. Records are upserted by name.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid

from api.config import get_settings
from api.db import get_session_factory
from api.models.document import Folder, Tag
from api.models.org import Department, Group, Org, Region, Role
from api.models.user import UserOrgMembership, UserProfile
from sqlalchemy import select, text

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
    result = await session.execute(select(model).filter_by(**filters))
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
            session,
            Org,
            filters={"name": E2E_ORG_NAME},
            defaults={"permission_number": 1, "description": "Created by seed_e2e"},
        )
        org_id: uuid.UUID = org.id

        # Site admin user
        admin = await _get_or_create(
            session,
            UserProfile,
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
                    session,
                    Region,
                    filters={"org_id": org_id, "name": name},
                    defaults={"permission_number": i},
                )
            )
        for i, name in enumerate(DEPARTMENTS, start=1):
            await _get_or_create(
                session,
                Department,
                filters={"org_id": org_id, "name": name},
                defaults={"permission_number": i},
            )
        for i, name in enumerate(ROLES, start=1):
            await _get_or_create(
                session,
                Role,
                filters={"org_id": org_id, "name": name},
                defaults={"permission_number": i},
            )
        for i, name in enumerate(GROUPS, start=1):
            await _get_or_create(
                session,
                Group,
                filters={"org_id": org_id, "name": name},
                defaults={"permission_number": i},
            )

        # Membership
        membership = await _get_or_create(
            session,
            UserOrgMembership,
            filters={"profile_id": admin.id, "org_id": org_id},
            defaults={"is_org_admin": True},
        )
        # Link admin to first region so permission mask is non-zero for chain tests.
        # Eager-load the m2m collection first — accessing an unloaded relationship
        # lazily under async SQLAlchemy raises "greenlet_spawn has not been called".
        await session.refresh(membership, attribute_names=["regions"])
        if not membership.regions:
            membership.regions = [created_regions[0]]

        # Additional non-admin members referenced by the RBAC E2E specs
        # (regular_user, user_a, user_b). The API auto-provisions these profiles
        # on first X-Test-User request, but the specs need them to already be
        # MEMBERS of this org so org-scoped endpoints resolve (a bare provisioned
        # user with no membership gets 403). Their subs match the API bypass
        # convention (`e2e-<username>`, see auth/dependencies._resolve_e2e_user),
        # so the later auto-provision reuses these rows instead of duplicating.
        # region North vs South gives user_a / user_b disjoint permission masks.
        south = created_regions[1] if len(created_regions) > 1 else created_regions[0]
        member_specs = [
            ("regular_user", "test@example.com", created_regions[0]),
            ("user_a", "usera@example.com", created_regions[0]),
            ("user_b", "userb@example.com", south),
        ]
        for m_username, m_email, m_region in member_specs:
            m_profile = await _get_or_create(
                session,
                UserProfile,
                filters={"keycloak_sub": f"e2e-{m_username}"},
                defaults={"username": m_username, "email": m_email, "is_site_admin": False},
            )
            m_membership = await _get_or_create(
                session,
                UserOrgMembership,
                filters={"profile_id": m_profile.id, "org_id": org_id},
                defaults={"is_org_admin": False},
            )
            await session.refresh(m_membership, attribute_names=["regions"])
            if not m_membership.regions:
                m_membership.regions = [m_region]

        # Folders
        await _get_or_create(
            session,
            Folder,
            filters={"org_id": org_id, "name": "Public", "parent_id": None},
            defaults={"dot_path": "Public", "view_permission_masks": []},
        )
        await _get_or_create(
            session,
            Folder,
            filters={"org_id": org_id, "name": "Engineering", "parent_id": None},
            defaults={"dot_path": "Engineering", "view_permission_masks": []},
        )

        # Tags
        for name in TAGS:
            await _get_or_create(
                session,
                Tag,
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
