"""Unit tests for claim-sync behaviour in ``provision_user_from_claims``.

Regression cover for the ``user_profiles`` lock storm: the provisioning helper
used to overwrite a stored username/email with a *sub-derived placeholder*
whenever a token arrived without those claims. Two clients for the same user
(a Clerk JWT-template token that carries ``email``, and a default session token
that does not) therefore flipped the row back and forth on alternating
requests, so nearly every authenticated request issued an UPDATE.

Both columns are UNIQUE-indexed, so Postgres escalates such an UPDATE to a
``FOR UPDATE`` tuple lock. Held for the life of the request, that lock blocks
the ``FOR KEY SHARE`` lock any INSERT referencing the row needs (chat_sessions,
documents) — and since those inserts run on a *second* pooled connection, the
request blocked on itself until ``statement_timeout`` killed it 30s later.

The rule these tests pin: **only an authoritative claim may overwrite a stored
value.** A missing claim leaves the column alone.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from api.models.user import UserProfile
from api.services.user_provisioning import provision_user_from_claims

SUB = "user_3G3R0wOG2MpbtqRb85EEFOPrw9v"
PLACEHOLDER_EMAIL = f"{SUB}@placeholder.invalid"


class _Result:
    def __init__(self, profile: UserProfile | None) -> None:
        self._profile = profile

    def scalar_one_or_none(self) -> UserProfile | None:
        return self._profile


class _FakeSession:
    """Minimal AsyncSession stand-in that records writes.

    ``flush_count`` is the assertion that matters: it is the proxy for "did we
    emit an UPDATE and therefore take a row lock".
    """

    def __init__(self, profile: UserProfile | None) -> None:
        self._profile = profile
        self.flush_count = 0
        self.added: list[Any] = []

    async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result(self._profile)

    async def flush(self) -> None:
        self.flush_count += 1

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _existing(*, username: str = "alice", email: str = "alice@example.com") -> UserProfile:
    return UserProfile(
        id=uuid.uuid4(),
        auth_subject=SUB,
        username=username,
        email=email,
        is_site_admin=False,
        is_active=True,
    )


# --- the flap: a claimless token must not clobber stored values --------------


async def test_missing_email_claim_does_not_overwrite_stored_email() -> None:
    profile = _existing()
    session = _FakeSession(profile)

    result = await provision_user_from_claims(session, sub=SUB, username="alice", email="")  # type: ignore[arg-type]

    assert result.email == "alice@example.com"
    assert session.flush_count == 0, "claimless token must not write (and must not lock the row)"


async def test_missing_username_claim_does_not_overwrite_stored_username() -> None:
    profile = _existing()
    session = _FakeSession(profile)

    result = await provision_user_from_claims(session, sub=SUB, username="", email="alice@example.com")  # type: ignore[arg-type]

    assert result.username == "alice"
    assert session.flush_count == 0


async def test_fully_claimless_token_is_a_pure_read() -> None:
    """The default Clerk session token carries neither claim — no write at all."""
    profile = _existing()
    session = _FakeSession(profile)

    await provision_user_from_claims(session, sub=SUB, username="", email="")  # type: ignore[arg-type]

    assert session.flush_count == 0


async def test_placeholder_email_is_never_written_back_over_a_real_one() -> None:
    """Guards the exact value that caused the alternating UPDATE."""
    profile = _existing(email="jeremy@example.com")
    session = _FakeSession(profile)

    await provision_user_from_claims(session, sub=SUB, username="", email="")  # type: ignore[arg-type]

    assert profile.email == "jeremy@example.com"
    assert profile.email != PLACEHOLDER_EMAIL


# --- authoritative claims still sync ----------------------------------------


async def test_real_claim_upgrades_a_stored_placeholder() -> None:
    """Once a JWT template is configured, the real values must take over."""
    profile = _existing(username=SUB, email=PLACEHOLDER_EMAIL)
    session = _FakeSession(profile)

    result = await provision_user_from_claims(session, sub=SUB, username="alice", email="alice@example.com")  # type: ignore[arg-type]

    assert result.username == "alice"
    assert result.email == "alice@example.com"
    assert session.flush_count == 1


async def test_changed_claim_still_syncs() -> None:
    profile = _existing(email="old@example.com")
    session = _FakeSession(profile)

    result = await provision_user_from_claims(session, sub=SUB, username="alice", email="new@example.com")  # type: ignore[arg-type]

    assert result.email == "new@example.com"
    assert session.flush_count == 1


async def test_unchanged_claims_do_not_write() -> None:
    profile = _existing()
    session = _FakeSession(profile)

    await provision_user_from_claims(session, sub=SUB, username="alice", email="alice@example.com")  # type: ignore[arg-type]

    assert session.flush_count == 0


# --- first-time provisioning keeps its fallbacks ----------------------------


async def test_new_profile_falls_back_to_sub_derived_values() -> None:
    session = _FakeSession(None)

    result = await provision_user_from_claims(session, sub=SUB, username="", email="")  # type: ignore[arg-type]

    assert result.auth_subject == SUB
    assert result.username == SUB
    assert result.email == PLACEHOLDER_EMAIL
    assert session.added == [result]
    assert session.flush_count == 1


async def test_new_profile_uses_real_claims_when_present() -> None:
    session = _FakeSession(None)

    result = await provision_user_from_claims(session, sub=SUB, username="alice", email="alice@example.com")  # type: ignore[arg-type]

    assert result.username == "alice"
    assert result.email == "alice@example.com"


@pytest.mark.parametrize("blank", ["", None])
async def test_blank_claims_are_treated_as_absent(blank: str | None) -> None:
    """Clerk omits the claim entirely on some tokens; jose yields None there."""
    profile = _existing()
    session = _FakeSession(profile)

    await provision_user_from_claims(session, sub=SUB, username=blank, email=blank)  # type: ignore[arg-type]

    assert session.flush_count == 0
    assert profile.email == "alice@example.com"


# --- SITE_ADMIN_EMAILS: pre-authorizing an admin who has never signed in -----


@pytest.fixture
def allowlist():
    """Build a Settings carrying just the allow-list under test."""
    from api.config import Settings

    def _make(raw: str) -> Settings:
        return Settings(site_admin_emails_raw=raw, secret_key="test-secret")  # type: ignore[arg-type]

    return _make


async def test_a_listed_email_is_a_site_admin_from_its_first_login(allowlist) -> None:
    """The gap this closes: a profile row exists only after a login, so an admin
    who has never signed in cannot be promoted by any console."""
    session = _FakeSession(None)

    result = await provision_user_from_claims(
        session,  # type: ignore[arg-type]
        sub=SUB,
        username="boss",
        email="boss@example.com",
        settings=allowlist("boss@example.com"),
    )

    assert result.is_site_admin is True


async def test_an_existing_profile_is_promoted_on_its_next_login(allowlist) -> None:
    profile = _existing()
    session = _FakeSession(profile)

    result = await provision_user_from_claims(
        session,  # type: ignore[arg-type]
        sub=SUB,
        username="alice",
        email="alice@example.com",
        settings=allowlist("alice@example.com"),
    )

    assert result.is_site_admin is True
    assert session.flush_count == 1


async def test_an_unlisted_user_is_never_promoted(allowlist) -> None:
    profile = _existing()
    session = _FakeSession(profile)

    result = await provision_user_from_claims(
        session,  # type: ignore[arg-type]
        sub=SUB,
        username="alice",
        email="alice@example.com",
        settings=allowlist("boss@example.com"),
    )

    assert result.is_site_admin is False
    # And the pure-read property of an unchanged row still holds — this must not
    # reintroduce the per-request UPDATE the tests above exist to prevent.
    assert session.flush_count == 0


async def test_removing_an_address_does_not_demote(allowlist) -> None:
    """The grant is one-way on purpose. An env change is not an audited action, so
    it must not silently strip someone's access mid-session; demotion belongs to
    the site-admin console, which records who did it."""
    profile = _existing()
    profile.is_site_admin = True
    session = _FakeSession(profile)

    result = await provision_user_from_claims(
        session,  # type: ignore[arg-type]
        sub=SUB,
        username="alice",
        email="alice@example.com",
        settings=allowlist(""),
    )

    assert result.is_site_admin is True
    assert session.flush_count == 0


async def test_a_claimless_token_cannot_satisfy_the_allowlist(allowlist) -> None:
    """Without a JWT template the session token carries no email, and provisioning
    substitutes a sub-derived placeholder. Matching on that would grant admin from
    a value this code invented rather than one the IdP vouched for."""
    session = _FakeSession(None)

    result = await provision_user_from_claims(
        session,  # type: ignore[arg-type]
        sub=SUB,
        username="",
        email="",
        settings=allowlist(PLACEHOLDER_EMAIL),
    )

    assert result.is_site_admin is False
