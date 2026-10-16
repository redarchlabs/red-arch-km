"""Lexical search + document reads against a real Qdrant.

The reader's unit tests fake the store; these pin the parts only the server can
answer: that the full-text index matches words in any case, that it is created
on a collection which predates it, and that every lexical read applies the same
access-key and folder filters as ranked search.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from brain_sdk.document_text import DocumentTextReader, ReadScope
from brain_sdk.vector_store.protocol import VectorRecord
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore

pytestmark = pytest.mark.integration

PUBLIC = 0
MEMBER = 1_111
RESTRICTED = 2_222


def _chunk(
    tenant: str,
    doc_key: str,
    order: int,
    text: str,
    *,
    access_keys: list[int] | None = None,
    tags: list[str] | None = None,
    section: str | None = None,
) -> VectorRecord:
    return VectorRecord(
        id=str(uuid.uuid4()),
        vector=[0.5, 0.5, 0.5, 0.5],
        payload={
            "text": text,
            "chunk_order": order,
            "section": section,
            "document_key": doc_key,
            "document_title": doc_key.title(),
            "tenant_id": tenant,
            "tags": tags or [],
            "access_keys": access_keys or [PUBLIC],
            "type": "chunk",
        },
    )


@pytest.fixture
def tenant(vector_store: QdrantVectorStore) -> str:
    tenant_id = f"t-{uuid.uuid4().hex[:8]}"
    vector_store.ensure_collections(tenant_id)
    vector_store.upsert_vectors(
        tenant_id,
        [
            _chunk(tenant_id, "pumps", 0, "The plant has six pumps.", section="Pumps"),
            _chunk(tenant_id, "pumps", 1, "ATLAS unit, asset tag PMP-2041-X, rated 40 kW.", section="Pumps"),
            _chunk(tenant_id, "pumps", 2, "The Vortex is the newest pump.", section="Pumps › Vortex"),
            _chunk(
                tenant_id,
                "hr-notes",
                0,
                "Atlas overtime costs are confidential.",
                access_keys=[RESTRICTED],
            ),
            _chunk(tenant_id, "ops", 0, "Atlas maintenance schedule.", tags=["folder:ops"]),
        ],
    )
    return tenant_id


def _keys(result: dict[str, Any]) -> set[str]:
    return {d["document_key"] for d in result["documents"]}


class TestTextIndex:
    def test_matches_words_in_any_case(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        hits = vector_store.text_search(tenant, ["atlas"])
        assert {h.payload["document_key"] for h in hits} == {"pumps", "hr-notes", "ops"}

    def test_a_word_is_not_a_substring(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        """The index matches whole words: "pump" finds "the newest pump." but not
        "six pumps". A caller after a prefix has to name the longer word."""
        orders = {h.payload["chunk_order"] for h in vector_store.text_search(tenant, ["pump"])}
        assert orders == {2}

    def test_every_word_of_a_term_is_required(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        assert {h.payload["chunk_order"] for h in vector_store.text_search(tenant, ["atlas asset"])} == {1}

    def test_any_mode_ors_the_terms(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        hits = vector_store.text_search(tenant, ["vortex", "tag"], match_all=False)
        assert {h.payload["chunk_order"] for h in hits if h.payload["document_key"] == "pumps"} == {1, 2}

    def test_index_is_added_to_a_collection_that_predates_it(self, vector_store: QdrantVectorStore) -> None:
        tenant_id = f"t-{uuid.uuid4().hex[:8]}"
        # A collection created the old way: no text index.
        vector_store._create_collection(vector_store._chunk_collection(tenant_id))
        vector_store.upsert_vectors(tenant_id, [_chunk(tenant_id, "legacy", 0, "Turbine Maintenance Log")])
        assert "text" not in (
            vector_store._client.get_collection(vector_store._chunk_collection(tenant_id)).payload_schema or {}
        )

        hits = vector_store.text_search(tenant_id, ["turbine"])

        assert [h.payload["document_key"] for h in hits] == ["legacy"]
        schema = vector_store._client.get_collection(vector_store._chunk_collection(tenant_id)).payload_schema
        assert "text" in schema

    def test_a_tenant_with_nothing_ingested_finds_nothing(self, vector_store: QdrantVectorStore) -> None:
        assert vector_store.text_search(f"t-{uuid.uuid4().hex[:8]}", ["anything"]) == []


class TestVisibility:
    def test_restricted_chunks_never_reach_a_member_without_the_mask(
        self, vector_store: QdrantVectorStore, tenant: str
    ) -> None:
        reader = DocumentTextReader(vector_store)
        member = ReadScope(access_keys=[PUBLIC, MEMBER])
        assert "hr-notes" not in _keys(reader.grep(tenant, "atlas", scope=member))
        assert "hr-notes" in _keys(reader.grep(tenant, "atlas", scope=ReadScope(access_keys=[PUBLIC, RESTRICTED])))
        assert reader.read(tenant, "hr-notes", scope=member) is None

    def test_folder_tags_limit_both_tools(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        reader = DocumentTextReader(vector_store)
        ops_only = ReadScope(any_tags=["folder:ops"])
        assert _keys(reader.grep(tenant, "atlas", scope=ops_only)) == {"ops"}
        assert reader.read(tenant, "pumps", scope=ops_only) is None
        assert reader.read(tenant, "ops", scope=ops_only) is not None

    def test_an_explicit_empty_scope_reads_nothing(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        reader = DocumentTextReader(vector_store)
        assert reader.grep(tenant, "atlas", scope=ReadScope(access_keys=[]))["documents"] == []
        assert reader.read(tenant, "pumps", scope=ReadScope(access_keys=[])) is None


class TestEndToEnd:
    def test_grep_finds_the_identifier_and_quotes_it_as_written(
        self, vector_store: QdrantVectorStore, tenant: str
    ) -> None:
        out = DocumentTextReader(vector_store).grep(tenant, "PMP-2041-X", scope=ReadScope(access_keys=[PUBLIC]))
        assert _keys(out) == {"pumps"}
        hit = out["documents"][0]["snippets"][0]
        assert hit["chunk"] == 1
        assert "ATLAS unit, asset tag PMP-2041-X" in hit["text"]

    def test_read_returns_the_document_in_order_with_headings(
        self, vector_store: QdrantVectorStore, tenant: str
    ) -> None:
        out = DocumentTextReader(vector_store).read(tenant, "pumps", scope=ReadScope(access_keys=[PUBLIC]))
        assert out is not None
        assert out["total_chunks"] == 3 and out["next_chunk"] is None
        text = out["text"]
        assert text.index("six pumps") < text.index("PMP-2041-X") < text.index("newest pump")
        assert "## Pumps › Vortex" in text

    def test_read_pages_from_a_chunk(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        reader = DocumentTextReader(vector_store)
        out = reader.read(tenant, "pumps", scope=ReadScope(), start_chunk=1, max_chars=60)
        assert out is not None
        assert out["start_chunk"] == 1 and out["next_chunk"] == 2
        assert "six pumps" not in out["text"]


class TestIrregularNumbering:
    """Documents ingested by older code can have duplicate chunks (re-ingest used
    to append) and, in principle, gaps. A paged read must neither loop nor stop
    early on either."""

    def test_duplicates_and_gaps_read_once_through_to_the_end(self, vector_store: QdrantVectorStore) -> None:
        tenant_id = f"t-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant_id)
        orders = [*range(5), *range(5), *range(90, 93)]  # every early chunk twice, then a gap
        vector_store.upsert_vectors(tenant_id, [_chunk(tenant_id, "old", o, f"Part {o}.") for o in orders])
        reader = DocumentTextReader(vector_store)

        out = reader.read(tenant_id, "old", scope=ReadScope(access_keys=[PUBLIC]))

        assert out is not None
        assert out["text"] == "Part 0. Part 1. Part 2. Part 3. Part 4. Part 90. Part 91. Part 92."
        assert out["next_chunk"] is None

    def test_has_chunks_from_respects_visibility(self, vector_store: QdrantVectorStore, tenant: str) -> None:
        assert vector_store.has_chunks_from(tenant, "pumps", start=2)
        assert not vector_store.has_chunks_from(tenant, "pumps", start=3)
        assert not vector_store.has_chunks_from(tenant, "hr-notes", start=0, access_keys=[PUBLIC, MEMBER])
