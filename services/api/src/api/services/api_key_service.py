"""API key lifecycle: mint, list, revoke.

Keys are opaque bearer secrets of the form ``km2_<token>`` where ``<token>`` is
32 bytes of URL-safe randomness. Only the **SHA-256 hash** of the full key is
persisted (:func:`hash_key`); the plaintext is returned to the caller exactly
once, at creation, and never stored or logged. Authentication later re-hashes the
presented key and matches it against :class:`ApiKey.key_hash`.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.api_key import ACCESS_MODE_ORG, ACCESS_MODE_SCOPED, ApiKey
from api.repositories.api_key import ApiKeyRepository
from api.services.api_key_assignments import (
    ApiKeyAssignmentError,
    AssignmentLabels,
    KeyAssignments,
    check_constraints_now,
    load_assignment_labels,
    save_assignments,
    validate_assignments,
)
from api.services.api_key_scopes import normalize_scopes, validate_scoped_key_scopes

_KEY_PREFIX = "km2_"
# Max keys per org — a guard against unbounded growth / abuse, not a hard product
# limit. Comfortably above any realistic integration count.
MAX_KEYS_PER_ORG = 100


class ApiKeyError(Exception):
    """Base class for API-key service errors."""


class ApiKeyNotFoundError(ApiKeyError):
    """The requested key does not exist in this org."""


class ApiKeyValidationError(ApiKeyError):
    """The request was invalid (bad scopes, over the per-org limit, etc.)."""


class ApiKeyAssignmentInvalid(ApiKeyValidationError):  # noqa: N818 - reads as the condition
    """An assignment cannot be granted: an unknown id, a folder the assignments
    cannot see, too many masks, or a folder set too wide to search (422)."""


class ApiKeyConflictError(ApiKeyError):
    """Something the key was being minted with changed underneath it (409)."""


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    """A freshly minted key: the one-time plaintext plus its stored derivatives."""

    plaintext: str
    prefix: str
    key_hash: str


def hash_key(plaintext: str) -> str:
    """Return the SHA-256 hex digest used to store and look up a key."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_key() -> GeneratedKey:
    """Mint a new random key and derive its display prefix + storage hash."""
    plaintext = f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    # First 12 chars ("km2_" + 8 of the token) — enough to recognise a key in the
    # admin list without revealing anything useful.
    prefix = plaintext[:12]
    return GeneratedKey(plaintext=plaintext, prefix=prefix, key_hash=hash_key(plaintext))


def is_expired(api_key: ApiKey, *, now: datetime | None = None) -> bool:
    """Whether the key's expiry has passed (keys without an expiry never expire).

    Expiry is inclusive (a key is expired *at* its expiry instant). A naive
    ``expires_at`` is treated as UTC so a malformed row can never raise a
    naive-vs-aware ``TypeError`` on the auth path (which would surface as a 500
    instead of a clean 401)."""
    expires_at = api_key.expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) >= expires_at


class ApiKeyService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = ApiKeyRepository(session, org_id)

    async def list_keys(self) -> list[ApiKey]:
        return await self._repo.list_all()

    async def create_key(
        self,
        *,
        name: str,
        scopes: list[str],
        expires_at: datetime | None,
        created_by_profile_id: uuid.UUID | None,
        assignments: KeyAssignments | None = None,
    ) -> tuple[ApiKey, str]:
        """Create a key and return ``(persisted_key, plaintext)``.

        The plaintext is the ONLY time the secret is available — callers must
        surface it to the user immediately and never persist it, and must COMMIT
        before returning it: a key whose row never committed would be a plaintext
        secret for nothing.

        Non-empty ``assignments`` make the key *scoped*: it reads knowledge with the
        masks of those regions/roles/groups/departments, narrowed to the listed
        folders. Every id must be in this org, the masks must stay under the cap,
        and every folder must be visible to them (``api.services.api_key_assignments``).
        A scoped key may hold only scope-aware scopes, never a wildcard.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ApiKeyValidationError("Key name is required")
        try:
            clean_scopes = normalize_scopes(scopes)
        except ValueError as exc:
            raise ApiKeyValidationError(str(exc)) from exc
        if not clean_scopes:
            raise ApiKeyValidationError("At least one scope is required")
        if expires_at is not None and expires_at <= datetime.now(UTC):
            raise ApiKeyValidationError("Expiry must be in the future")
        if await self._repo.count() >= MAX_KEYS_PER_ORG:
            raise ApiKeyValidationError(f"This organization has reached the maximum of {MAX_KEYS_PER_ORG} API keys")
        scoped = assignments is not None and not assignments.is_empty
        if assignments is not None and scoped:
            try:
                validate_scoped_key_scopes(clean_scopes)
            except ValueError as exc:
                raise ApiKeyValidationError(str(exc)) from exc
            try:
                await validate_assignments(self._session, self._org_id, assignments)
            except ApiKeyAssignmentError as exc:
                raise ApiKeyAssignmentInvalid(str(exc)) from exc

        generated = generate_key()
        api_key = ApiKey(
            name=clean_name,
            key_prefix=generated.prefix,
            key_hash=generated.key_hash,
            scopes=clean_scopes,
            expires_at=expires_at,
            created_by_profile_id=created_by_profile_id,
            access_mode=ACCESS_MODE_SCOPED if scoped else ACCESS_MODE_ORG,
        )
        await self._repo.create(api_key)
        if assignments is not None and scoped:
            await save_assignments(self._session, api_key.id, assignments)
            try:
                # The item FKs are deferred to commit; check them now so a row
                # deleted under the mint is a conflict here, not a lost key later.
                await check_constraints_now(self._session)
            except IntegrityError as exc:
                raise ApiKeyConflictError(
                    "One of the chosen regions, roles, groups, departments or folders was deleted while the key "
                    "was being created. Reload and try again."
                ) from exc
        return api_key, generated.plaintext

    async def assignment_labels(self, keys: list[ApiKey]) -> dict[uuid.UUID, AssignmentLabels]:
        """Each scoped key's assignments with names, for the admin list."""
        return await load_assignment_labels(self._session, [k.id for k in keys if k.access_mode == ACCESS_MODE_SCOPED])

    async def revoke_key(self, api_key_id: uuid.UUID) -> ApiKey:
        """Revoke a key. Idempotent — revoking an already-revoked key is a no-op."""
        api_key = await self._repo.get(api_key_id)
        if api_key is None:
            raise ApiKeyNotFoundError("API key not found")
        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(UTC)
            await self._session.flush()
        return api_key
