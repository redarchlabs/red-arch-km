"""An agent reads the knowledge base with its actor's eyes (real PostgreSQL).

``search_knowledge`` used to pass a ``tenant_id`` and nothing else, while every
member-facing search passed the caller's permission mask. So an agent retrieved
from the entire organisation regardless of who set it running, and could quote a
document to someone with no clearance to read it. An agent answers in prose, so
that disclosure arrived without the citation trail that would make it obvious.

These pin the resolution itself rather than the retrieval, because the mask list
is the whole security boundary: brain-api filters on exactly what it is handed.

The sentinel matters as much as the masks. Ingest records an unconfigured document
as the public key ``0``, and retrieval is a MatchAny over the document's stored
keys — so a mask list that omits ``0`` matches no public document at all. Getting
that wrong does not fail loudly; it silently returns an empty knowledge base,
which reads like "the KB is broken" rather than "you lack access".
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Department, Org, Region
from api.models.user import UserOrgMembership, UserProfile
from api.services.search_access import UNRESTRICTED_MASK, resolve_profile_access_keys
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org(admin_session: AsyncSession, permission_number: int = 1) -> Org:
    org = Org(name=f"KB-{uuid.uuid4().hex[:8]}", permission_number=permission_number)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    return org


async def _member(
    admin_session: AsyncSession,
    org: Org,
    *,
    is_org_admin: bool = False,
    is_site_admin: bool = False,
    regions: list[Region] | None = None,
    departments: list[Department] | None = None,
) -> UserProfile:
    suffix = uuid.uuid4().hex[:8]
    profile = UserProfile(
        auth_subject=f"sub-{suffix}",
        username=f"user-{suffix}",
        email=f"user-{suffix}@example.test",
        is_site_admin=is_site_admin,
    )
    admin_session.add(profile)
    await admin_session.flush()
    membership = UserOrgMembership(profile_id=profile.id, org_id=org.id, is_org_admin=is_org_admin)
    membership.regions = regions or []
    membership.departments = departments or []
    admin_session.add(membership)
    await admin_session.flush()
    return profile


class TestActorScoping:
    async def test_an_org_admins_agent_is_unrestricted(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        profile = await _member(admin_session, org, is_org_admin=True)

        assert await resolve_profile_access_keys(admin_session, org.id, profile.id) is None

    async def test_a_site_admins_agent_is_unrestricted_without_a_membership(self, admin_session: AsyncSession) -> None:
        """require_org_access synthesises a membership for a site admin; the agent
        path must grant the same reach or a site admin's agent would see nothing."""
        org = await _org(admin_session)
        suffix = uuid.uuid4().hex[:8]
        profile = UserProfile(
            auth_subject=f"sub-{suffix}",
            username=f"user-{suffix}",
            email=f"user-{suffix}@example.test",
            is_site_admin=True,
        )
        admin_session.add(profile)
        await admin_session.flush()

        assert await resolve_profile_access_keys(admin_session, org.id, profile.id) is None

    async def test_a_plain_member_is_restricted_but_still_sees_public_content(
        self, admin_session: AsyncSession
    ) -> None:
        """The regression that would look like a broken KB: without the public
        sentinel a restricted member matches no unconfigured document, i.e. every
        document in the system today."""
        org = await _org(admin_session)
        profile = await _member(admin_session, org)

        keys = await resolve_profile_access_keys(admin_session, org.id, profile.id)

        assert keys is not None  # restricted, NOT org-wide
        assert UNRESTRICTED_MASK in keys

    async def test_dimensions_widen_the_mask_without_dropping_the_sentinel(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        region = Region(name="West", permission_number=3, org_id=org.id)
        dept = Department(name="Ops", permission_number=2, org_id=org.id)
        admin_session.add_all([region, dept])
        await admin_session.flush()
        profile = await _member(admin_session, org, regions=[region], departments=[dept])

        keys = await resolve_profile_access_keys(admin_session, org.id, profile.id)

        assert keys is not None
        assert UNRESTRICTED_MASK in keys
        # Its own dimension mask is there too — the member is not reduced to public.
        assert len(keys) > 1

    async def test_a_profile_with_no_membership_gets_nothing(self, admin_session: AsyncSession) -> None:
        """Empty is not None. Returning None here would hand org-wide reach to a
        profile that is not in the org at all — the exact inversion to avoid."""
        org = await _org(admin_session)
        other_org = await _org(admin_session, permission_number=2)
        profile = await _member(admin_session, other_org)
        await set_tenant(admin_session, str(org.id))

        assert await resolve_profile_access_keys(admin_session, org.id, profile.id) == []

    async def test_two_members_of_different_orgs_do_not_share_masks(self, admin_session: AsyncSession) -> None:
        org_a = await _org(admin_session, permission_number=1)
        profile_a = await _member(admin_session, org_a)
        org_b = await _org(admin_session, permission_number=2)
        profile_b = await _member(admin_session, org_b)

        keys_a = await resolve_profile_access_keys(admin_session, org_a.id, profile_a.id)
        keys_b = await resolve_profile_access_keys(admin_session, org_b.id, profile_b.id)

        assert keys_a is not None and keys_b is not None
        # Only the public sentinel is shared; the org-encoded masks differ.
        assert set(keys_a) & set(keys_b) == {UNRESTRICTED_MASK}
