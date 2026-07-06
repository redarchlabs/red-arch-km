"""Integration tests: first-run setup claim + site-admin console queries.

Runs against a real PostgreSQL (testcontainers) with the RLS policies from
conftest applied, and fakeredis standing in for Redis. The `admin_session`
fixture is the superuser login — the same trust level the API's runtime role
has in deployment (cf. /users/me reading memberships without tenant context).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from api.models.org import Org
from api.models.user import UserOrgMembership, UserProfile
from api.repositories.membership import MembershipRepository
from api.repositories.user import UserRepository
from api.services.setup_token import consume_setup_token, ensure_setup_token, site_admin_exists
from fakeredis import aioredis as fakeaioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@pytest.fixture
def redis() -> Any:
    return fakeaioredis.FakeRedis(decode_responses=True)


def _user(username: str, *, is_site_admin: bool = False, is_active: bool = True) -> UserProfile:
    return UserProfile(
        auth_subject=f"sub-{username}-{uuid.uuid4().hex[:8]}",
        username=f"{username}-{uuid.uuid4().hex[:6]}",
        email=f"{username}-{uuid.uuid4().hex[:6]}@example.com",
        is_site_admin=is_site_admin,
        is_active=is_active,
    )


async def test_full_claim_flow(admin_session: AsyncSession, redis: Any) -> None:
    """Boot with no admin → token issued → claim promotes → token gone → second boot silent."""
    user = _user("claimer")
    admin_session.add(user)
    await admin_session.flush()

    assert await site_admin_exists(admin_session) is False
    token = await ensure_setup_token(admin_session, redis, ttl_seconds=60)
    assert token is not None

    # Wrong guess doesn't burn the token; right one consumes it exactly once.
    assert await consume_setup_token(redis, "nope") is False
    assert await consume_setup_token(redis, token) is True
    user.is_site_admin = True
    await admin_session.flush()

    assert await site_admin_exists(admin_session) is True
    assert await consume_setup_token(redis, token) is False
    # Next boot: admin exists → no token generated, stale key cleared.
    assert await ensure_setup_token(admin_session, redis, ttl_seconds=60) is None


async def test_deactivated_admin_does_not_count(admin_session: AsyncSession) -> None:
    admin_session.add(_user("inactive-admin", is_site_admin=True, is_active=False))
    await admin_session.flush()
    assert await site_admin_exists(admin_session) is False


async def test_admin_user_queries(admin_session: AsyncSession) -> None:
    repo = UserRepository(admin_session)
    active_admin = _user("alpha-admin", is_site_admin=True)
    plain = _user("beta-user")
    admin_session.add_all([active_admin, plain])
    await admin_session.flush()

    users, total = await repo.list_all(q="beta-")
    assert total >= 1
    assert any(u.id == plain.id for u in users)
    assert all("beta-" in u.username or "beta-" in u.email for u in users)

    assert await repo.count_active_site_admins() >= 1

    # LIKE metacharacters are literals, not wildcards: "%" alone matches only
    # names containing a literal percent sign (none seeded here).
    _, wildcard_total = await repo.list_all(q="%")
    assert wildcard_total == 0


async def test_cross_org_membership_listing(admin_session: AsyncSession) -> None:
    """The global console lists a user's memberships across ALL orgs without
    tenant context — must work at the API's runtime trust level."""
    repo = UserRepository(admin_session)
    user = _user("multi-org")
    org_a = Org(name=f"Org-A-{uuid.uuid4().hex[:6]}", permission_number=1)
    org_b = Org(name=f"Org-B-{uuid.uuid4().hex[:6]}", permission_number=2)
    admin_session.add_all([user, org_a, org_b])
    await admin_session.flush()
    admin_session.add_all(
        [
            UserOrgMembership(profile_id=user.id, org_id=org_a.id, is_org_admin=True),
            UserOrgMembership(profile_id=user.id, org_id=org_b.id, is_org_admin=False),
        ]
    )
    await admin_session.flush()

    rows = await repo.list_memberships_with_orgs(user.id)
    assert [(org.id, m.is_org_admin) for m, org in rows] == [
        (org_a.id, True),
        (org_b.id, False),
    ]


async def test_membership_delete_respects_rls(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    """DELETE via the tenant session only sees the membership under the right GUC."""
    user = _user("deletee")
    org = Org(name=f"Org-Del-{uuid.uuid4().hex[:6]}", permission_number=3)
    admin_session.add_all([user, org])
    await admin_session.flush()
    membership = UserOrgMembership(profile_id=user.id, org_id=org.id)
    admin_session.add(membership)
    await admin_session.commit()

    try:
        # Wrong tenant context: both the explicit org_id filter (bound to a
        # different org) and FORCE RLS hide the row → repo reports missing.
        wrong_org = uuid.uuid4()
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(wrong_org)},
        )
        assert await MembershipRepository(session, wrong_org).delete(membership.id) is False
        await session.rollback()

        # Correct tenant context: delete succeeds.
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org.id)},
        )
        assert await MembershipRepository(session, org.id).delete(membership.id) is True
        await session.commit()
    finally:
        # Row-level cleanup of committed data (schema is session-scoped).
        await admin_session.execute(
            text("DELETE FROM user_org_memberships WHERE id = :mid"), {"mid": str(membership.id)}
        )
        await admin_session.commit()
