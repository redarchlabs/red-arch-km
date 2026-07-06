"""Unit tests for the knowledge-gap detection + in-memory log."""

from __future__ import annotations

from brain_sdk.facts.gaps import (
    GapStatus,
    InMemoryGapLog,
    assess_gap,
    build_gap,
    dedup_key,
)


def _ev(tool: str, rows: int) -> dict:
    return {"id": "E1", "tool": tool, "args": {}, "result": [{"x": i} for i in range(rows)]}


class TestAssessGap:
    def test_fact_tool_zero_rows_is_gap(self) -> None:
        a = assess_gap([_ev("claim_query", 0)])
        assert a.is_gap
        assert a.fact_rows == 0
        assert a.tools_used == ("claim_query",)

    def test_fact_tool_with_rows_is_not_gap(self) -> None:
        assert not assess_gap([_ev("claim_query", 3)]).is_gap

    def test_only_passage_search_is_not_a_fact_gap(self) -> None:
        # search_passages found text but no fact tool was tried — not a fact gap.
        assert not assess_gap([_ev("search_passages", 0)]).is_gap

    def test_mixed_fact_rows_counted_across_tools(self) -> None:
        a = assess_gap([_ev("claim_query", 0), _ev("entity_lookup", 0), _ev("search_passages", 2)])
        assert a.is_gap  # both fact tools empty despite passages hitting
        assert a.fact_rows == 0

    def test_no_evidence_is_not_a_gap(self) -> None:
        assert not assess_gap([]).is_gap


class TestDedupKey:
    def test_case_and_punctuation_insensitive(self) -> None:
        assert dedup_key("Who is the CMO?") == dedup_key("who is the  cmo")


class TestInMemoryGapLog:
    def _gap(self, log_id: str, question: str, tenant: str = "t1", now: str = "2026-07-06T00:00:00Z"):
        return build_gap(
            gap_id=log_id,
            tenant_id=tenant,
            question=question,
            answer="no info",
            assessment=assess_gap([_ev("claim_query", 0)]),
            now=now,
        )

    def test_record_and_list_open(self) -> None:
        log = InMemoryGapLog()
        log.record(self._gap("g1", "Who is the CMO?"))
        gaps = log.list_open("t1")
        assert len(gaps) == 1
        assert gaps[0].question == "Who is the CMO?"
        assert gaps[0].fact_rows == 0

    def test_recurring_question_folds_and_counts(self) -> None:
        log = InMemoryGapLog()
        log.record(self._gap("g1", "Who is the CMO?", now="2026-07-06T00:00:00Z"))
        folded = log.record(self._gap("g2", "who is the cmo", now="2026-07-06T01:00:00Z"))
        assert folded.occurrences == 2
        assert folded.last_seen_at == "2026-07-06T01:00:00Z"
        assert len(log.list_open("t1")) == 1  # one gap, not two

    def test_tenant_isolation(self) -> None:
        log = InMemoryGapLog()
        log.record(self._gap("g1", "Q", tenant="t1"))
        log.record(self._gap("g2", "Q", tenant="t2"))
        assert len(log.list_open("t1")) == 1
        assert len(log.list_open("t2")) == 1

    def test_set_status_resolves_and_hides(self) -> None:
        log = InMemoryGapLog()
        log.record(self._gap("g1", "Q"))
        assert log.set_status("t1", "g1", GapStatus.RESOLVED, resolved_at="2026-07-06T02:00:00Z")
        assert log.list_open("t1") == []

    def test_set_status_unknown_returns_false(self) -> None:
        assert not InMemoryGapLog().set_status("t1", "nope", GapStatus.DISMISSED)

    def test_resolved_gap_does_not_fold_new_occurrence(self) -> None:
        log = InMemoryGapLog()
        log.record(self._gap("g1", "Q"))
        log.set_status("t1", "g1", GapStatus.RESOLVED)
        # Same question comes back after resolution → a fresh open gap.
        log.record(self._gap("g2", "Q"))
        assert len(log.list_open("t1")) == 1
