"""Unit tests for API-key generation, hashing, expiry, and create validation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.services.api_key_service import (
    ApiKeyService,
    ApiKeyValidationError,
    generate_key,
    hash_key,
    is_expired,
)


class TestKeyPrimitives:
    def test_generated_key_shape(self) -> None:
        gen = generate_key()
        assert gen.plaintext.startswith("km2_")
        assert gen.prefix == gen.plaintext[:12]
        assert gen.key_hash == hash_key(gen.plaintext)
        assert len(gen.key_hash) == 64  # sha256 hex

    def test_keys_are_unique(self) -> None:
        assert generate_key().plaintext != generate_key().plaintext

    def test_hash_is_deterministic(self) -> None:
        assert hash_key("km2_abc") == hash_key("km2_abc")

    def test_is_expired(self) -> None:
        past = SimpleNamespace(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        future = SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(hours=1))
        never = SimpleNamespace(expires_at=None)
        assert is_expired(past)
        assert not is_expired(future)
        assert not is_expired(never)

    def test_is_expired_at_exact_boundary(self) -> None:
        # Expiry is inclusive: a key is expired AT its expiry instant.
        at = datetime.now(UTC)
        assert is_expired(SimpleNamespace(expires_at=at), now=at)

    def test_is_expired_tolerates_naive_expiry(self) -> None:
        # A naive expires_at (malformed row) is treated as UTC, not a 500-causing
        # naive-vs-aware TypeError on the auth path.
        naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        naive_future = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        assert is_expired(SimpleNamespace(expires_at=naive_past))
        assert not is_expired(SimpleNamespace(expires_at=naive_future))


def _service_with_repo(repo: MagicMock) -> ApiKeyService:
    svc = ApiKeyService(MagicMock(), uuid.uuid4())
    svc._repo = repo  # type: ignore[attr-defined]
    return svc


class TestCreateValidation:
    async def test_rejects_blank_name(self) -> None:
        repo = MagicMock(count=AsyncMock(return_value=0), create=AsyncMock())
        with pytest.raises(ApiKeyValidationError, match="name is required"):
            await _service_with_repo(repo).create_key(
                name="  ", scopes=["reports:run"], expires_at=None, created_by_profile_id=None
            )

    async def test_rejects_empty_scopes(self) -> None:
        repo = MagicMock(count=AsyncMock(return_value=0), create=AsyncMock())
        with pytest.raises(ApiKeyValidationError, match="scope"):
            await _service_with_repo(repo).create_key(
                name="k", scopes=[], expires_at=None, created_by_profile_id=None
            )

    async def test_rejects_unknown_scope(self) -> None:
        repo = MagicMock(count=AsyncMock(return_value=0), create=AsyncMock())
        with pytest.raises(ApiKeyValidationError, match="Unknown scope"):
            await _service_with_repo(repo).create_key(
                name="k", scopes=["bogus:x"], expires_at=None, created_by_profile_id=None
            )

    async def test_rejects_past_expiry(self) -> None:
        repo = MagicMock(count=AsyncMock(return_value=0), create=AsyncMock())
        with pytest.raises(ApiKeyValidationError, match="future"):
            await _service_with_repo(repo).create_key(
                name="k",
                scopes=["reports:run"],
                expires_at=datetime.now(UTC) - timedelta(hours=1),
                created_by_profile_id=None,
            )

    async def test_enforces_per_org_limit(self) -> None:
        repo = MagicMock(count=AsyncMock(return_value=100), create=AsyncMock())
        with pytest.raises(ApiKeyValidationError, match="maximum"):
            await _service_with_repo(repo).create_key(
                name="k", scopes=["reports:run"], expires_at=None, created_by_profile_id=None
            )

    async def test_allows_up_to_the_limit(self) -> None:
        # One below the cap must still succeed (guards the >= boundary).
        repo = MagicMock(count=AsyncMock(return_value=99), create=AsyncMock(side_effect=lambda k: k))
        _, plaintext = await _service_with_repo(repo).create_key(
            name="k", scopes=["reports:run"], expires_at=None, created_by_profile_id=None
        )
        assert plaintext.startswith("km2_")

    async def test_happy_path_returns_plaintext_and_persists_hash(self) -> None:
        created: dict = {}

        async def _create(api_key):  # noqa: ANN001, ANN202
            created["key"] = api_key
            return api_key

        repo = MagicMock(count=AsyncMock(return_value=0), create=AsyncMock(side_effect=_create))
        svc = _service_with_repo(repo)
        api_key, plaintext = await svc.create_key(
            name="Integration", scopes=["reports:run", "reports:run"], expires_at=None, created_by_profile_id=None
        )
        assert plaintext.startswith("km2_")
        # Only the hash is persisted, never the plaintext.
        assert created["key"].key_hash == hash_key(plaintext)
        assert plaintext not in (created["key"].key_prefix,)
        assert api_key.scopes == ["reports:run"]  # deduped
