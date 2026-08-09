"""A streaming endpoint must not hold a pooled connection for the whole stream.

``require_org_access`` takes ``get_db``, and FastAPI exits yield-dependencies only
*after* the response finishes. For an ordinary JSON endpoint that is a few
milliseconds. For SSE it is the entire life of the connection — so a membership
lookup pinned a connection that then sat idle, because a stream does no database
work between frames.

The rule these pin: a connection is held while there is read or write work to do,
and released the moment there isn't. Waiting is not work.
"""

from __future__ import annotations

import uuid

import pytest
from api import dependencies as deps_module
from api.auth.dependencies import CurrentUser, require_org_access_streaming
from api.config import get_settings
from api.models.org import Org
from api.models.user import UserOrgMembership, UserProfile
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


@pytest.fixture
def container_factory(engine: AsyncEngine, monkeypatch):
    """Point the short-lived auth session at the test container.

    ``auth_provisioning_session`` builds its factory from the process-wide engine,
    which in the suite is never wired to the container — the other integration
    tests use the ``engine`` fixture directly.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(deps_module, "get_session_factory", lambda _settings: factory)
    return factory


async def _seed(admin_session: AsyncSession) -> tuple[Org, UserProfile]:
    org = Org(name=f"Stream-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    suffix = uuid.uuid4().hex[:8]
    profile = UserProfile(
        auth_subject=f"sub-{suffix}",
        username=f"user-{suffix}",
        email=f"user-{suffix}@example.test",
    )
    admin_session.add(profile)
    await admin_session.flush()
    admin_session.add(UserOrgMembership(profile_id=profile.id, org_id=org.id, is_org_admin=True))
    await admin_session.commit()
    return org, profile


def _user(profile: UserProfile) -> CurrentUser:
    return CurrentUser(
        sub=profile.auth_subject,
        username=profile.username,
        email=profile.email,
        profile_id=profile.id,
        is_site_admin=False,
    )


class TestStreamingAuth:
    async def test_it_resolves_the_same_context(self, admin_session: AsyncSession, container_factory) -> None:
        org, profile = await _seed(admin_session)

        ctx = await require_org_access_streaming(user=_user(profile), org_id=org.id, settings=get_settings())

        assert ctx.org_id == org.id
        assert ctx.is_org_admin is True

    async def test_it_leaves_no_connection_checked_out(
        self, admin_session: AsyncSession, engine: AsyncEngine, container_factory
    ) -> None:
        """The whole point. The ordinary dependency's session stays checked out
        until the response ends; this one must be back in the pool before the
        stream even starts."""
        org, profile = await _seed(admin_session)
        pool = engine.pool

        before = pool.checkedout()
        await require_org_access_streaming(user=_user(profile), org_id=org.id, settings=get_settings())

        assert pool.checkedout() == before

    async def test_the_context_survives_its_session_closing(
        self, admin_session: AsyncSession, container_factory
    ) -> None:
        """Membership relationships are eager-loaded, so nothing lazy-loads against
        the closed session afterwards — which on an async session would surface as
        MissingGreenlet mid-stream rather than at resolution time."""
        org, profile = await _seed(admin_session)

        ctx = await require_org_access_streaming(user=_user(profile), org_id=org.id, settings=get_settings())

        # Touching the collections must not need the (now closed) session.
        assert ctx.membership.regions == []
        assert ctx.membership.departments == []
        assert ctx.membership.roles == []
        assert ctx.membership.groups == []
