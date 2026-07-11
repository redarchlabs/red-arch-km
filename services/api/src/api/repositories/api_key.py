"""Repository for API keys — org-scoped like the other tenant repos.

Two access patterns coexist here:

* **Org-scoped management** (``list_all`` / ``count`` / ``get`` / ``create``) is
  used by the admin surface under the current org and, in keeping with the sibling
  repos, filters every query by ``org_id`` explicitly for defence in depth on top
  of RLS. (Revocation and the ``last_used_at`` touch are performed by the service
  layer and the auth path respectively, not here.)
* **Cross-tenant auth lookup** (module-level :func:`lookup_by_key_hash`) runs on a
  privileged session during authentication — before any org is known — so it
  deliberately does *not* filter by org. The globally-unique ``key_hash``
  guarantees a single match.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.api_key import ApiKey


async def lookup_by_key_hash(session: AsyncSession, key_hash: str) -> ApiKey | None:
    """Resolve a presented key by its SHA-256 hash across all tenants.

    Used by the authentication path on a privileged session — before any org is
    known — so it is deliberately un-scoped. The globally-unique ``key_hash``
    guarantees at most one match.
    """
    result = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    return result.scalar_one_or_none()


class ApiKeyRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[ApiKey]:
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.org_id == self._org_id).order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(ApiKey).where(ApiKey.org_id == self._org_id)
        )
        return int(result.scalar_one())

    async def get(self, api_key_id: uuid.UUID) -> ApiKey | None:
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.id == api_key_id, ApiKey.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, api_key: ApiKey) -> ApiKey:
        api_key.org_id = self._org_id
        self._session.add(api_key)
        await self._session.flush()
        return api_key

