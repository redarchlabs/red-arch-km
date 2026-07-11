"""Integration tests for API-key storage: org scoping, RLS isolation, and the
hash-lookup auth path against a real PostgreSQL.

Two isolation layers are asserted:
* explicit ``org_id`` filtering in the repository (run on the superuser
  ``admin_session``, which bypasses RLS — mirrors test_repo_org_scoping.py); and
* the database RLS policy on ``api_keys`` (run on the ``app_user`` ``session``
  with a tenant context set).
"""

from __future__ import annotations

import uuid

import pytest
from api.models.api_key import ApiKey
from api.models.org import Org
from api.models.user import UserProfile
from api.repositories.api_key import ApiKeyRepository, lookup_by_key_hash
from api.services.api_key_service import ApiKeyService, hash_key
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed_two_orgs(admin_session: AsyncSession) -> tuple[Org, Org, UserProfile]:
    org_a = Org(name=f"KeyA-{uuid.uuid4().hex[:8]}", permission_number=1)
    org_b = Org(name=f"KeyB-{uuid.uuid4().hex[:8]}", permission_number=1)
    user = UserProfile(
        auth_subject=f"key-{uuid.uuid4()}",
        username=f"key_{uuid.uuid4().hex[:8]}",
        email=f"key_{uuid.uuid4().hex[:8]}@test.local",
    )
    admin_session.add_all([org_a, org_b, user])
    await admin_session.commit()
    return org_a, org_b, user


class TestApiKeyRepositoryScoping:
    async def test_list_is_org_scoped_even_without_rls(self, admin_session: AsyncSession) -> None:
        org_a, org_b, user = await _seed_two_orgs(admin_session)
        _, plaintext_a = await ApiKeyService(admin_session, org_a.id).create_key(
            name="A", scopes=["reports:run"], expires_at=None, created_by_profile_id=user.id
        )
        await ApiKeyService(admin_session, org_b.id).create_key(
            name="B", scopes=["records:read"], expires_at=None, created_by_profile_id=user.id
        )
        await admin_session.commit()

        a_keys = await ApiKeyRepository(admin_session, org_a.id).list_all()
        assert [k.name for k in a_keys] == ["A"]
        # The hash lookup resolves the presented key to org A across tenants.
        found = await lookup_by_key_hash(admin_session, hash_key(plaintext_a))
        assert found is not None and found.org_id == org_a.id

    async def test_revoke_sets_timestamp_idempotently(self, admin_session: AsyncSession) -> None:
        org_a, _org_b, user = await _seed_two_orgs(admin_session)
        svc = ApiKeyService(admin_session, org_a.id)
        api_key, _ = await svc.create_key(
            name="R", scopes=["reports:run"], expires_at=None, created_by_profile_id=user.id
        )
        await admin_session.commit()

        revoked = await svc.revoke_key(api_key.id)
        assert revoked.revoked_at is not None
        first = revoked.revoked_at
        # Revoking again is a no-op (does not move the timestamp).
        again = await svc.revoke_key(api_key.id)
        assert again.revoked_at == first


class TestApiKeyRLS:
    async def test_rls_hides_other_orgs_keys(
        self, session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        org_a, org_b, user = await _seed_two_orgs(admin_session)
        admin_session.add_all(
            [
                ApiKey(name="A", key_prefix="km2_a", key_hash=hash_key(f"km2_{uuid.uuid4()}"),
                       scopes=["reports:run"], org_id=org_a.id, created_by_profile_id=user.id),
                ApiKey(name="B", key_prefix="km2_b", key_hash=hash_key(f"km2_{uuid.uuid4()}"),
                       scopes=["records:read"], org_id=org_b.id, created_by_profile_id=user.id),
            ]
        )
        await admin_session.commit()

        # app_user session scoped to org A sees only org A's key.
        await set_tenant(session, str(org_a.id))
        visible = (await session.execute(select(ApiKey.org_id))).scalars().all()
        assert set(visible) == {org_a.id}

        # Re-scope to org B: only org B's key is visible.
        await set_tenant(session, str(org_b.id))
        visible_b = (await session.execute(select(ApiKey.org_id))).scalars().all()
        assert set(visible_b) == {org_b.id}
