"""Resolve the API key for a provider: an org's own key wins, central is fallback.

Mirrors the precedence already used for OpenAI in the workflow runner
(``ActionExecutor._org_openai_key``): decrypt the org's stored key if present,
otherwise fall back to the central key in settings. Returns ``None`` when neither
is configured so callers can surface a clear "no key for provider" error.
"""

from __future__ import annotations

import uuid

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.repositories.org_provider_credential import OrgProviderCredentialRepository
from api.services.crypto import decrypt_secret


def central_provider_key(provider: str, settings: Settings) -> str | None:
    """The central (deployment-wide) key for ``provider``, or None if unset."""
    mapping: dict[str, SecretStr] = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
    }
    secret = mapping.get(provider)
    value = secret.get_secret_value() if secret else ""
    return value or None


async def resolve_provider_key(
    session: AsyncSession,
    org_id: uuid.UUID,
    provider: str,
    settings: Settings,
) -> str | None:
    """Return the key to use for ``provider`` in ``org_id`` (org key ?? central)."""
    repo = OrgProviderCredentialRepository(session, org_id)
    cred = await repo.get_by_provider(provider)
    if cred and cred.secret_encrypted:
        return decrypt_secret(cred.secret_encrypted, settings.org_encryption_key.get_secret_value())
    return central_provider_key(provider, settings)
