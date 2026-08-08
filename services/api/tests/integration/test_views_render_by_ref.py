"""Integration tests for slug-addressed view rendering (``render_by_ref``).

The runtime render endpoint resolves its path segment UUID-first, slug-fallback.
The slug is only org-unique, so the security property worth proving against the
real repository + RLS stack is *isolation*: two orgs can own the same slug, and
each session resolves only its own view — a slug known from one tenant is not a
handle into another.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.schemas.form import FormConfig
from api.schemas.view import ViewCreate
from api.services.form_service import FormNotFoundError
from api.services.view_service import ViewService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

SLUG = "course_play"


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def _make_standalone_view(session: AsyncSession, org: Org, name: str) -> uuid.UUID:
    await set_tenant(session, str(org.id))
    view = await ViewService(session, org.id).create_view(
        ViewCreate(
            name=name,
            slug=SLUG,
            config=FormConfig.model_validate({"version": 2, "elements": [{"type": "label", "text": name}]}),
        )
    )
    await session.commit()
    return view.id


class TestRenderByRefTenantIsolation:
    async def test_same_slug_resolves_to_each_orgs_own_view(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org_a = await _make_org(admin_session, "REF-ISO-A")
        org_b = await _make_org(admin_session, "REF-ISO-B")
        view_a = await _make_standalone_view(session, org_a, "Player A")
        view_b = await _make_standalone_view(session, org_b, "Player B")

        await set_tenant(session, str(org_a.id))
        read_a = await ViewService(session, org_a.id).render_by_ref(SLUG, None)
        assert read_a.form_id == view_a

        await set_tenant(session, str(org_b.id))
        read_b = await ViewService(session, org_b.id).render_by_ref(SLUG, None)
        assert read_b.form_id == view_b

    async def test_another_orgs_slug_is_not_found(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        org_a = await _make_org(admin_session, "REF-ISO-ONLY-A")
        org_b = await _make_org(admin_session, "REF-ISO-ONLY-B")
        await _make_standalone_view(session, org_a, "Player A")

        # Org B knows the slug exists in org A; resolving it from B's context fails.
        await set_tenant(session, str(org_b.id))
        with pytest.raises(FormNotFoundError):
            await ViewService(session, org_b.id).render_by_ref(SLUG, None)

    async def test_another_orgs_view_id_is_not_found_either(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org_a = await _make_org(admin_session, "REF-ISO-ID-A")
        org_b = await _make_org(admin_session, "REF-ISO-ID-B")
        view_a = await _make_standalone_view(session, org_a, "Player A")

        # The UUID branch is org-scoped the same way.
        await set_tenant(session, str(org_b.id))
        with pytest.raises(FormNotFoundError):
            await ViewService(session, org_b.id).render_by_ref(str(view_a), None)
