"""Unit tests for the predicate ontology and normalisation."""

from __future__ import annotations

from brain_sdk.facts.predicates import Cardinality, PredicateRegistry, slug_predicate


class TestSlug:
    def test_snake_cases(self) -> None:
        assert slug_predicate("Is Managed By") == "is_managed_by"

    def test_strips_punctuation(self) -> None:
        assert slug_predicate("  works-under!! ") == "works_under"


class TestRegistry:
    def setup_method(self) -> None:
        self.reg = PredicateRegistry()

    def test_exact_key(self) -> None:
        spec = self.reg.normalize("headquartered_in")
        assert spec.key == "headquartered_in"
        assert spec.cardinality is Cardinality.FUNCTIONAL

    def test_alias_maps_to_canonical(self) -> None:
        assert self.reg.normalize("is managed by").key == "reports_to"
        assert self.reg.normalize("based in").key == "headquartered_in"

    def test_unknown_predicate_defaults_multi(self) -> None:
        spec = self.reg.normalize("collaborates with")
        assert spec.key == "collaborates_with"
        assert spec.cardinality is Cardinality.MULTI

    def test_cardinality_lookup(self) -> None:
        assert self.reg.cardinality("headquartered_in") is Cardinality.FUNCTIONAL
        assert self.reg.cardinality("authored") is Cardinality.MULTI
        assert self.reg.cardinality("totally_unknown") is Cardinality.MULTI

    def test_keys_include_seed(self) -> None:
        keys = self.reg.keys()
        assert "reports_to" in keys
        assert "acquired" in keys
