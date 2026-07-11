"""Unit tests for the API-key scope catalog + matching logic."""

from __future__ import annotations

import pytest
from api.services.api_key_scopes import VALID_SCOPES, has_scope, normalize_scopes


class TestHasScope:
    def test_exact_match(self) -> None:
        assert has_scope(frozenset({"reports:run"}), "reports:run")

    def test_missing_scope_denied(self) -> None:
        assert not has_scope(frozenset({"reports:read"}), "reports:run")

    def test_global_wildcard_grants_everything(self) -> None:
        assert has_scope(frozenset({"*"}), "workflows:run")
        assert has_scope(frozenset({"*"}), "knowledge:read")

    def test_domain_wildcard_grants_only_its_domain(self) -> None:
        granted = frozenset({"reports:*"})
        assert has_scope(granted, "reports:run")
        assert has_scope(granted, "reports:read")
        assert not has_scope(granted, "workflows:run")

    def test_empty_grant_denies(self) -> None:
        assert not has_scope(frozenset(), "reports:read")


class TestNormalizeScopes:
    def test_dedupes_and_orders_by_catalog(self) -> None:
        result = normalize_scopes(["reports:run", "entities:read", "reports:run"])
        # Catalog order: entities:read comes before reports:run.
        assert result == ["entities:read", "reports:run"]

    def test_accepts_wildcards(self) -> None:
        assert normalize_scopes(["*"]) == ["*"]
        assert normalize_scopes(["reports:*"]) == ["reports:*"]

    def test_wildcard_and_concrete_scope_both_kept(self) -> None:
        # A concrete scope + its covering wildcard are both retained (not merged) —
        # documenting the current, deliberate non-semantic-dedup behavior.
        assert normalize_scopes(["reports:run", "reports:*"]) == ["reports:run", "reports:*"]

    def test_multiple_wildcards_sorted_after_catalog_scopes(self) -> None:
        assert normalize_scopes(["records:write", "*", "reports:*"]) == [
            "records:write",
            "*",
            "reports:*",
        ]

    def test_rejects_unknown_scope(self) -> None:
        with pytest.raises(ValueError, match="Unknown scope"):
            normalize_scopes(["reports:run", "bogus:action"])

    def test_rejects_unknown_domain_wildcard(self) -> None:
        with pytest.raises(ValueError, match="Unknown scope"):
            normalize_scopes(["nope:*"])

    def test_every_catalog_scope_is_valid(self) -> None:
        assert normalize_scopes(sorted(VALID_SCOPES)) == [
            s for s in _catalog_order() if s in VALID_SCOPES
        ]


def _catalog_order() -> list[str]:
    from api.services.api_key_scopes import API_SCOPES

    return [s.name for s in API_SCOPES]
