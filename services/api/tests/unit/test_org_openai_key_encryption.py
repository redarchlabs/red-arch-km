"""Unit tests for per-org OpenAI key encryption at the router boundary.

Proves the WRITE path (orgs PATCH) stores ciphertext and the internal READ path
returns decrypted plaintext, with the DB session/repo/factory mocked out.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from api.config import Settings
from api.routers import internal as internal_module
from api.routers import orgs as orgs_module
from api.schemas.org import OrgUpdate
from api.services.crypto import decrypt_secret, encrypt_secret

ORG_ID = uuid.uuid4()
ENCRYPTION_SECRET = "router-test-encryption-secret"  # noqa: S105 - test fixture, not a real credential


@pytest.fixture
def settings() -> Settings:
    return Settings(
        secret_key="test-secret",
        database_url="postgresql+asyncpg://t:t@localhost/t",
        org_encryption_key=ENCRYPTION_SECRET,
    )


class _FakeOrg(SimpleNamespace):
    pass


class _FakeOrgRepo:
    """Stand-in for OrgRepository that records the value handed to update()."""

    instance: _FakeOrgRepo | None = None

    def __init__(self, _session: Any) -> None:
        self.org = _FakeOrg(
            id=ORG_ID,
            name="Acme",
            description=None,
            use_knowledge_graph=True,
            openai_api_key=None,
        )
        _FakeOrgRepo.instance = self

    async def get(self, _org_id: uuid.UUID) -> _FakeOrg:
        return self.org

    async def update(self, org: _FakeOrg, **kwargs: Any) -> _FakeOrg:
        for attr in ("name", "description", "use_knowledge_graph"):
            if kwargs.get(attr) is not None:
                setattr(org, attr, kwargs[attr])
        if kwargs.get("openai_api_key") is not None:
            org.openai_api_key = kwargs["openai_api_key"] or None
        return org


@pytest.mark.asyncio
async def test_update_org_stores_ciphertext(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(orgs_module, "OrgRepository", _FakeOrgRepo)
    plaintext = "sk-plaintext-key-value"

    await orgs_module.update_org(
        ORG_ID,
        OrgUpdate(openai_api_key=plaintext),
        _admin=MagicMock(),
        session=MagicMock(),
        settings=settings,
    )

    stored = _FakeOrgRepo.instance.org.openai_api_key
    assert stored is not None
    assert stored != plaintext  # encrypted at rest
    assert plaintext not in stored
    assert decrypt_secret(stored, ENCRYPTION_SECRET) == plaintext


@pytest.mark.asyncio
async def test_update_org_empty_string_clears_key(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(orgs_module, "OrgRepository", _FakeOrgRepo)
    _FakeOrgRepo.instance = None

    await orgs_module.update_org(
        ORG_ID,
        OrgUpdate(openai_api_key=""),
        _admin=MagicMock(),
        session=MagicMock(),
        settings=settings,
    )
    assert _FakeOrgRepo.instance.org.openai_api_key is None


@pytest.mark.asyncio
async def test_update_org_none_leaves_key_untouched(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(orgs_module, "OrgRepository", _FakeOrgRepo)
    _FakeOrgRepo.instance = None

    await orgs_module.update_org(
        ORG_ID,
        OrgUpdate(name="Renamed"),
        _admin=MagicMock(),
        session=MagicMock(),
        settings=settings,
    )
    assert _FakeOrgRepo.instance.org.openai_api_key is None
    assert _FakeOrgRepo.instance.org.name == "Renamed"


# --------------------------------------------------------------------------- #
# Read path (internal endpoint) decrypts before returning.
# --------------------------------------------------------------------------- #


class _FakeSession:
    def __init__(self, org: Any) -> None:
        self._org = org

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, *_a: Any, **_kw: Any) -> Any:
        return MagicMock()

    async def get(self, _model: Any, _pk: Any) -> Any:
        return self._org


@pytest.mark.asyncio
async def test_get_org_openai_key_returns_decrypted_plaintext(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    plaintext = "sk-worker-key"
    org = _FakeOrg(id=ORG_ID, openai_api_key=encrypt_secret(plaintext, ENCRYPTION_SECRET))
    monkeypatch.setattr(internal_module, "get_session_factory", lambda _s: (lambda: _FakeSession(org)))

    result = await internal_module.get_org_openai_key(ORG_ID, settings=settings)
    assert result.openai_api_key == plaintext


@pytest.mark.asyncio
async def test_get_org_openai_key_tolerates_legacy_plaintext(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    # A row still holding plaintext (pre-migration) must not 500.
    org = _FakeOrg(id=ORG_ID, openai_api_key="sk-legacy-plaintext")
    monkeypatch.setattr(internal_module, "get_session_factory", lambda _s: (lambda: _FakeSession(org)))

    result = await internal_module.get_org_openai_key(ORG_ID, settings=settings)
    assert result.openai_api_key == "sk-legacy-plaintext"


@pytest.mark.asyncio
async def test_get_org_openai_key_null_stays_null(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    org = _FakeOrg(id=ORG_ID, openai_api_key=None)
    monkeypatch.setattr(internal_module, "get_session_factory", lambda _s: (lambda: _FakeSession(org)))

    result = await internal_module.get_org_openai_key(ORG_ID, settings=settings)
    assert result.openai_api_key is None
