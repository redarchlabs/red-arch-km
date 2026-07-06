"""``_tenant_label`` sanitizer safety.

Every read/write in :class:`Neo4jFactStore` interpolates a ``:Tenant_<id>`` label
directly into Cypher (labels cannot be parameterized), so the sanitizer is the
sole barrier against label/Cypher injection via an adversarial ``tenant_id``.
This mirrors ``test_identifiers`` for the SQL side. The Neo4j driver constructs
lazily (no connection), so these run without a live database.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from brain_sdk.facts.neo4j_fact_store import Neo4jFactStore

# A label that is safe to inline is exactly: "Tenant_" + [A-Za-z0-9_]*
_SAFE_LABEL_RE = re.compile(r"^Tenant_[A-Za-z0-9_]*$")


@pytest.fixture(scope="module")
def store() -> Iterator[Neo4jFactStore]:
    s = Neo4jFactStore("bolt://localhost:7687", "neo4j", "unused")
    yield s
    s.close()


@pytest.mark.parametrize(
    "tenant_id",
    [
        "org-normal-123",
        "`); MATCH (n) DETACH DELETE n //",  # cypher injection via backtick
        "Tenant_x`",  # trailing backtick
        "a}b{c",  # braces
        "a//b",  # comment sequence
        "MATCH RETURN CREATE DELETE",  # cypher keywords (spaces → underscores)
        "café-münchen",  # unicode
        "汉字テスト",  # non-latin unicode
        "'; DROP;--",  # sql-ish
        "\n\t ",  # whitespace only
        "",  # empty
        "a" * 500,  # very long
        "123-456",  # leading digit
    ],
)
def test_tenant_label_is_injection_safe(store: Neo4jFactStore, tenant_id: str) -> None:
    label = store._tenant_label(tenant_id)
    assert label.startswith("Tenant_")
    # No character outside the allowlist survives — nothing that could break out
    # of the label position in Cypher (backtick, brace, slash, quote, semicolon,
    # whitespace, or any non-ASCII-word char).
    assert _SAFE_LABEL_RE.fullmatch(label), f"unsafe label produced: {label!r}"
    for forbidden in ("`", "{", "}", "/", "'", '"', ";", "-", " ", "\n", "\t"):
        assert forbidden not in label


def test_distinct_ids_are_deterministic(store: Neo4jFactStore) -> None:
    # Same input → same label (stable across calls, required for label matching).
    assert store._tenant_label("org-1") == store._tenant_label("org-1")
    assert store._tenant_label("org-1") == "Tenant_org_1"
