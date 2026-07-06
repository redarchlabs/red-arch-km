"""Unit tests for fact-engine models and id derivation."""

from __future__ import annotations

import pytest
from brain_sdk.facts.models import (
    Claim,
    Entity,
    ObjectType,
    compute_dedup_key,
    compute_entity_id,
)


class TestEntityId:
    def test_deterministic(self) -> None:
        a = compute_entity_id("t1", "ORG", "Acme Corp")
        b = compute_entity_id("t1", "ORG", "Acme Corp")
        assert a == b

    def test_case_and_whitespace_insensitive(self) -> None:
        assert compute_entity_id("t1", "ORG", "Acme Corp") == compute_entity_id("t1", "org", "  acme   corp ")

    def test_tenant_scoped(self) -> None:
        assert compute_entity_id("t1", "ORG", "Acme") != compute_entity_id("t2", "ORG", "Acme")

    def test_type_scoped(self) -> None:
        assert compute_entity_id("t1", "ORG", "Mercury") != compute_entity_id("t1", "PLANET", "Mercury")

    def test_entity_make_derives_id(self) -> None:
        e = Entity.make(tenant_id="t1", canonical_name="Acme", type="ORG")
        assert e.entity_id == compute_entity_id("t1", "ORG", "Acme")


class TestClaim:
    def test_literal_dedup_key(self) -> None:
        c = Claim(
            tenant_id="t1",
            subject_id="s1",
            predicate="headquartered_in",
            object_type=ObjectType.TEXT,
            object_value="Paris",
        )
        assert c.object_key == "Paris"
        assert c.dedup_key == compute_dedup_key("t1", "s1", "headquartered_in", "Paris")
        assert c.claim_id == c.dedup_key

    def test_entity_object_dedup_key_uses_object_id(self) -> None:
        c = Claim(
            tenant_id="t1",
            subject_id="s1",
            predicate="reports_to",
            object_type=ObjectType.ENTITY,
            object_id="e-boss",
        )
        assert c.object_key == "e-boss"
        assert c.dedup_key == compute_dedup_key("t1", "s1", "reports_to", "e-boss")

    def test_entity_claim_requires_object_id(self) -> None:
        with pytest.raises(ValueError, match="requires object_id"):
            Claim(tenant_id="t1", subject_id="s1", predicate="p", object_type=ObjectType.ENTITY)

    def test_literal_claim_requires_object_value(self) -> None:
        with pytest.raises(ValueError, match="requires object_value"):
            Claim(tenant_id="t1", subject_id="s1", predicate="p", object_type=ObjectType.TEXT)

    def test_same_fact_different_case_collapses(self) -> None:
        a = Claim(
            tenant_id="t1", subject_id="s1", predicate="Headquartered In",
            object_type=ObjectType.TEXT, object_value="Paris",
        )
        b = Claim(
            tenant_id="t1", subject_id="s1", predicate="headquartered in",
            object_type=ObjectType.TEXT, object_value="paris",
        )
        assert a.dedup_key == b.dedup_key
