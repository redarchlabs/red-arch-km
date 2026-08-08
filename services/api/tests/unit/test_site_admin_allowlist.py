"""Unit tests for SITE_ADMIN_EMAILS — pre-authorizing an admin who has never
signed in.

A ``UserProfile`` row only exists after a successful login, so before this there
was no way to make someone a site admin ahead of time: the first-run setup token
covers exactly one person exactly once, and every later admin has to be promoted
by an existing one. The tempting workaround — inserting a placeholder row with a
made-up ``auth_subject`` — is a trap, because provisioning matches on
``auth_subject`` alone: the real login would insert a *second* row and collide on
the UNIQUE email, turning that person's first sign-in into a 500.

The rules that make an env-var grant safe to ship are asserted here: it only fires
on an email the IdP actually asserted, and it never revokes.
"""

from __future__ import annotations

import pytest
from api.config import Settings
from api.services import user_provisioning

pytestmark = pytest.mark.unit


def _settings(raw: str) -> Settings:
    return Settings(site_admin_emails_raw=raw, secret_key="test-secret")  # type: ignore[arg-type]


class TestParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", frozenset()),
            ("a@b.com", frozenset({"a@b.com"})),
            ("a@b.com,c@d.com", frozenset({"a@b.com", "c@d.com"})),
            ("  a@b.com , c@d.com  ", frozenset({"a@b.com", "c@d.com"})),
            # IdPs differ on the case they assert for the same mailbox.
            ("Jeremy@Example.COM", frozenset({"jeremy@example.com"})),
            (",,", frozenset()),
        ],
    )
    def test_parses_the_list(self, raw: str, expected: frozenset[str]) -> None:
        assert _settings(raw).site_admin_emails == expected


class TestMatching:
    LIST = "boss@example.com"

    def test_a_listed_email_is_promoted(self) -> None:
        assert user_provisioning._should_be_site_admin(_settings(self.LIST), "boss@example.com", asserted=True) is True

    def test_case_and_whitespace_do_not_matter(self) -> None:
        assert (
            user_provisioning._should_be_site_admin(_settings(self.LIST), "  BOSS@Example.com ", asserted=True) is True
        )

    def test_an_unlisted_email_is_not(self) -> None:
        assert (
            user_provisioning._should_be_site_admin(_settings(self.LIST), "someone@example.com", asserted=True) is False
        )

    def test_an_email_the_idp_never_asserted_is_ignored(self) -> None:
        """Without a JWT template Clerk's session token carries no email claim, and
        provisioning substitutes a value it derived from the subject. Matching on
        that would let the allow-list be satisfied by a string this code made up
        rather than by anything the identity provider vouched for."""
        assert (
            user_provisioning._should_be_site_admin(_settings(self.LIST), "boss@example.com", asserted=False) is False
        )

    def test_an_empty_email_never_matches(self) -> None:
        assert user_provisioning._should_be_site_admin(_settings(self.LIST), "", asserted=True) is False


class TestEmptyAllowlist:
    def test_nothing_is_promoted_when_unset(self) -> None:
        """The default. Turning the feature off must not depend on the list being
        absent from the environment — an empty string has to mean empty too."""
        assert user_provisioning._should_be_site_admin(_settings(""), "boss@example.com", asserted=True) is False

    def test_no_settings_at_all_promotes_nobody(self) -> None:
        """Callers that predate the parameter (and any future one that forgets it)
        must fail closed rather than raising or reaching for a global."""
        assert user_provisioning._should_be_site_admin(None, "boss@example.com", asserted=True) is False
