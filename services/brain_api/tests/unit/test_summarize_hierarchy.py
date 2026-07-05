"""Tests for ChunkSummarizer.summarize_document_hierarchy tree construction.

No live OpenAI: we instantiate the summariser (its constructor only builds a
client object, no network) and monkeypatch `_summarize_group` so the grouping /
tree-building logic is exercised deterministically.
"""

from __future__ import annotations

from brain_sdk.summarization.chunk_summarizer import ChunkSummarizer


def _make_summarizer(**kwargs: int) -> ChunkSummarizer:
    summarizer = ChunkSummarizer(api_key="sk-test", **kwargs)
    # Deterministic group summary: join child summaries so we can assert
    # structure without any LLM round-trip.
    summarizer._summarize_group = lambda summaries: "+".join(s for s in summaries if s)  # type: ignore[method-assign]
    return summarizer


class TestSummarizeDocumentHierarchy:
    def test_empty_returns_none_tree(self) -> None:
        summary, tree = _make_summarizer().summarize_document_hierarchy([])
        assert summary == ""
        assert tree is None

    def test_single_summary_is_leaf(self) -> None:
        summary, tree = _make_summarizer().summarize_document_hierarchy(["only"])
        assert summary == "only"
        assert tree == {"summary": "only", "children": []}

    def test_two_level_tree_leaves_are_chunk_summaries(self) -> None:
        chunks = [f"c{i}" for i in range(3)]
        summary, tree = _make_summarizer(group_size=10).summarize_document_hierarchy(chunks)
        assert tree is not None
        # One group -> root over 3 leaves.
        assert summary == "c0+c1+c2"
        assert [leaf["summary"] for leaf in tree["children"]] == chunks
        assert all(leaf["children"] == [] for leaf in tree["children"])

    def test_multi_level_tree_when_groups_exceed_group_size(self) -> None:
        chunks = [f"c{i}" for i in range(6)]
        # group_size=2 -> level1 has 3 group nodes -> level2 rolls those into 2 -> level3 into 1.
        summary, tree = _make_summarizer(group_size=2).summarize_document_hierarchy(chunks)
        assert tree is not None
        # Root has children that are themselves internal nodes with their own children.
        assert tree["children"], "root must have children"
        first = tree["children"][0]
        assert first["children"], "internal node must retain its own children"
        # Deepest leaves are the original chunk summaries.
        node = tree
        while node["children"]:
            node = node["children"][0]
        assert node["summary"] in chunks

    def test_summarize_document_delegates_to_hierarchy(self) -> None:
        summarizer = _make_summarizer(group_size=10)
        assert summarizer.summarize_document(["a", "b"]) == "a+b"
