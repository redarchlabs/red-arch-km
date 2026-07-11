"""Unit tests for provider-key resolution (org key wins, central is fallback)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from api.config import Settings
from api.services.agents.llm import keys
from api.services.crypto import encrypt_secret

pytestmark = pytest.mark.unit


def _settings(**overrides) -> Settings:
    base = dict(
        secret_key=SecretStr("test-jwt-secret"),
        openai_api_key=SecretStr(""),
        anthropic_api_key=SecretStr(""),
        gemini_api_key=SecretStr(""),
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_central_provider_key_reads_the_right_secret():
    s = _settings(anthropic_api_key=SecretStr("ak"), openai_api_key=SecretStr("ok"))
    assert keys.central_provider_key("anthropic", s) == "ak"
    assert keys.central_provider_key("openai", s) == "ok"
    assert keys.central_provider_key("gemini", s) is None  # unset -> None
    assert keys.central_provider_key("unknown", s) is None


@pytest.mark.asyncio
async def test_resolve_prefers_org_key(monkeypatch):
    s = _settings(anthropic_api_key=SecretStr("central"))
    ciphertext = encrypt_secret("org-secret", s.org_encryption_key.get_secret_value())

    class _Repo:
        def __init__(self, session, org_id):
            pass

        async def get_by_provider(self, provider):
            return SimpleNamespace(secret_encrypted=ciphertext)

    monkeypatch.setattr(keys, "OrgProviderCredentialRepository", _Repo)
    got = await keys.resolve_provider_key(None, uuid4(), "anthropic", s)
    assert got == "org-secret"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_central(monkeypatch):
    s = _settings(anthropic_api_key=SecretStr("central"))

    class _Repo:
        def __init__(self, session, org_id):
            pass

        async def get_by_provider(self, provider):
            return None

    monkeypatch.setattr(keys, "OrgProviderCredentialRepository", _Repo)
    got = await keys.resolve_provider_key(None, uuid4(), "anthropic", s)
    assert got == "central"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_nothing_configured(monkeypatch):
    s = _settings()

    class _Repo:
        def __init__(self, session, org_id):
            pass

        async def get_by_provider(self, provider):
            return None

    monkeypatch.setattr(keys, "OrgProviderCredentialRepository", _Repo)
    assert await keys.resolve_provider_key(None, uuid4(), "openai", s) is None
