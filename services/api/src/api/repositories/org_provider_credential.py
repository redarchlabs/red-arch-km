"""Repository for per-org LLM provider credentials — org-scoped like the other
tenant repos. Stores/returns ciphertext only; the encryption key never lives here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.org_provider_credential import OrgProviderCredential


class OrgProviderCredentialRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[OrgProviderCredential]:
        result = await self._session.execute(
            select(OrgProviderCredential)
            .where(OrgProviderCredential.org_id == self._org_id)
            .order_by(OrgProviderCredential.provider)
        )
        return list(result.scalars().all())

    async def get_by_provider(self, provider: str) -> OrgProviderCredential | None:
        result = await self._session.execute(
            select(OrgProviderCredential).where(
                OrgProviderCredential.org_id == self._org_id,
                OrgProviderCredential.provider == provider,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, provider: str, secret_encrypted: str) -> OrgProviderCredential:
        """Create or replace the encrypted key for ``provider`` in this org."""
        existing = await self.get_by_provider(provider)
        if existing is not None:
            existing.secret_encrypted = secret_encrypted
            await self._session.flush()
            return existing
        cred = OrgProviderCredential(
            org_id=self._org_id, provider=provider, secret_encrypted=secret_encrypted
        )
        self._session.add(cred)
        await self._session.flush()
        return cred

    async def delete(self, provider: str) -> bool:
        existing = await self.get_by_provider(provider)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True
