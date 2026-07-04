"""Unit tests locking in the provider-neutral rename of the IdP-subject column.

D3 / AC-4.1: ``user_profiles.keycloak_sub`` was renamed to ``auth_subject`` so the
column name is provider-neutral after the Keycloak -> Clerk migration (Slice 6).
The Go stack (``services/api-go``) already shipped this rename (migration
``002_rename_keycloak_sub_to_auth_subject``); these tests assert the Python ORM,
repository, and provisioning surfaces reached parity with it.
"""

from __future__ import annotations

from api.models.user import UserProfile
from api.repositories.user import UserRepository


def test_user_profile_has_auth_subject_column() -> None:
    columns = set(UserProfile.__table__.columns.keys())
    assert "auth_subject" in columns
    assert "keycloak_sub" not in columns


def test_auth_subject_column_preserves_unique_index() -> None:
    col = UserProfile.__table__.columns["auth_subject"]
    # Rename must preserve the UNIQUE + indexed constraints of the old column.
    assert col.unique is True
    assert col.index is True
    # The auto-generated index name mirrors the Go migration's target name.
    index_names = {ix.name for ix in UserProfile.__table__.indexes}
    assert "ix_user_profiles_auth_subject" in index_names
    assert "ix_user_profiles_keycloak_sub" not in index_names


def test_user_profile_constructs_with_auth_subject() -> None:
    profile = UserProfile(
        auth_subject="user_clerk_abc123",
        username="alice",
        email="alice@example.com",
    )
    assert profile.auth_subject == "user_clerk_abc123"
    assert not hasattr(profile, "keycloak_sub")


def test_repository_exposes_get_by_auth_subject() -> None:
    assert hasattr(UserRepository, "get_by_auth_subject")
    assert not hasattr(UserRepository, "get_by_keycloak_sub")
