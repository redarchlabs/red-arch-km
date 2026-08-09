"""Who sits on a board, what they decided, and when it is settled.

About a tenth of what an agent produces is confident and wrong, and it looks
exactly like the nine tenths that are fine. These are the rules that decide when a
plan or a deliverable has actually been reviewed — kept pure so both gates share
one answer rather than drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from api.services.agents import review_board as rb

pytestmark = pytest.mark.unit


@dataclass
class _Entry:
    text: str


BOARDS = {
    "engineering": [
        {"agent": "devils-advocate", "lens": "Argue why this is wrong."},
        {"agent": "security-analyst", "lens": "Threat model."},
        {"agent": "principal-engineer", "lens": "Buildability."},
        {"agent": "requirements-auditor", "lens": "Coverage."},
    ],
    "business": [
        {"agent": "devils-advocate", "lens": "Argue why this is wrong."},
        {"agent": "research-analyst", "lens": "Evidence check."},
    ],
}


class TestChoosingTheBoard:
    def test_the_level_decides_how_many_sit(self) -> None:
        assert [s.agent for s in rb.resolve_board(BOARDS, level="light")] == ["devils-advocate"]
        assert len(rb.resolve_board(BOARDS, level="standard")) == 2
        assert len(rb.resolve_board(BOARDS, level="full")) == 4
        assert rb.resolve_board(BOARDS, level="none") == []

    def test_the_adversarial_lens_is_always_first(self) -> None:
        """It is the lens that catches the failure this exists for — an answer that
        is plausible and wrong — so it must survive the smallest board."""
        for level in ("light", "standard", "full"):
            assert rb.resolve_board(BOARDS, level=level)[0].agent == "devils-advocate"

    def test_the_author_is_never_on_its_own_board(self) -> None:
        """Writer is not reviewer — the property that makes a review mean anything."""
        seats = rb.resolve_board(BOARDS, level="standard", author="devils-advocate")

        assert "devils-advocate" not in [s.agent for s in seats]

    def test_dropping_the_author_does_not_shrink_the_board(self) -> None:
        # Otherwise an order authored by a board member quietly gets less review
        # than the level it asked for.
        seats = rb.resolve_board(BOARDS, level="standard", author="devils-advocate")

        assert len(seats) == 2

    def test_business_work_gets_business_lenses(self) -> None:
        """'Do the numbers hold' is not a question a security analyst answers."""
        seats = rb.resolve_board(BOARDS, level="standard", board_name="business")

        assert [s.agent for s in seats] == ["devils-advocate", "research-analyst"]

    def test_an_unknown_board_falls_back_rather_than_skipping_review(self) -> None:
        seats = rb.resolve_board(BOARDS, level="light", board_name="nonsense")

        assert [s.agent for s in seats] == ["devils-advocate"]


class TestVerdicts:
    def test_fail_dominates_on_the_line_the_verdict_is_declared_on(self) -> None:
        """A gate must not open on "passes, except…"."""
        assert rb.parse_verdict("PASS overall, but this would FAIL on load") == rb.FAIL
        assert rb.parse_verdict("PASS — nothing to add") == rb.PASS

    def test_findings_that_discuss_failure_do_not_overturn_a_pass(self) -> None:
        """Caught live: a security reviewer opened with PASS and its findings used
        the word "fail" in a sentence, so the whole-answer scan recorded a FAIL and
        sent the author back to fix an approval."""
        answer = (
            "PASS\n\n"
            "Findings (threat model):\n"
            "1) The webhook should fail closed if the signature is absent.\n"
            "2) Rate limits fail open today; consider tightening."
        )

        assert rb.parse_verdict(answer) == rb.PASS

    def test_a_reviewer_that_declares_nothing_is_not_an_approval(self) -> None:
        assert rb.parse_verdict("Looks fine to me, ship it.") == rb.FAIL

    def test_a_board_is_unsettled_until_everyone_reports(self) -> None:
        # The author's run stays parked meanwhile; resuming early would hand it one
        # reviewer's verdict and discard the rest.
        entries = [
            _Entry(rb.convene_marker("plan", "abc123abc123abc1", rb.resolve_board(BOARDS, level="standard"))),
            _Entry(rb.verdict_marker("devils-advocate", rb.PASS, "fine")),
        ]

        out = rb.outcome(entries, "plan")

        assert not out.settled
        assert out.pending == ["security-analyst"]

    def test_it_is_approved_only_when_every_seat_passed(self) -> None:
        seats = rb.resolve_board(BOARDS, level="standard")
        entries = [
            _Entry(rb.convene_marker("plan", "abc123abc123abc1", seats)),
            _Entry(rb.verdict_marker("devils-advocate", rb.PASS, "fine")),
            _Entry(rb.verdict_marker("security-analyst", rb.FAIL, "leaks the key")),
        ]

        out = rb.outcome(entries, "plan")

        assert out.settled and not out.approved
        assert out.failed == ["security-analyst"]

    def test_a_reviewer_can_change_its_mind(self) -> None:
        """Later wins, so a FAIL can become a PASS once the author fixes it —
        without needing a whole new round."""
        seats = rb.resolve_board(BOARDS, level="light")
        entries = [
            _Entry(rb.convene_marker("plan", "abc123abc123abc1", seats)),
            _Entry(rb.verdict_marker("devils-advocate", rb.FAIL, "no")),
            _Entry(rb.verdict_marker("devils-advocate", rb.PASS, "fixed")),
        ]

        assert rb.outcome(entries, "plan").approved

    def test_an_earlier_round_cannot_satisfy_a_later_one(self) -> None:
        """A PASS on the old plan says nothing about the revised one."""
        seats = rb.resolve_board(BOARDS, level="light")
        entries = [
            _Entry(rb.convene_marker("plan", "1111111111111111", seats)),
            _Entry(rb.verdict_marker("devils-advocate", rb.PASS, "fine")),
            _Entry(rb.convene_marker("plan", "2222222222222222", seats)),
        ]

        out = rb.outcome(entries, "plan")

        assert not out.settled and out.pending == ["devils-advocate"]

    def test_the_two_gates_do_not_answer_for_each_other(self) -> None:
        seats = rb.resolve_board(BOARDS, level="light")
        entries = [
            _Entry(rb.convene_marker("plan", "1111111111111111", seats)),
            _Entry(rb.verdict_marker("devils-advocate", rb.PASS, "fine")),
        ]

        assert rb.outcome(entries, "delivery").pending == []
        assert rb.outcome(entries, "plan").approved


class TestCostGuards:
    def test_an_unchanged_resubmission_has_the_same_fingerprint(self) -> None:
        """Reconvening on an identical plan is a wasted round — the reviewers would
        have nothing new to read."""
        assert rb.fingerprint("Crawl  the site\n") == rb.fingerprint("Crawl the site")
        assert rb.fingerprint("Crawl the site") != rb.fingerprint("Crawl the sitemap")

    def test_rounds_are_counted_per_gate(self) -> None:
        seats = rb.resolve_board(BOARDS, level="light")
        entries = [
            _Entry(rb.convene_marker("plan", "1111111111111111", seats)),
            _Entry(rb.convene_marker("plan", "2222222222222222", seats)),
            _Entry(rb.convene_marker("delivery", "3333333333333333", seats)),
        ]

        assert rb.rounds_run(entries, "plan") == rb.MAX_ROUNDS
        assert rb.rounds_run(entries, "delivery") == 1

    def test_a_passed_gate_does_not_bless_a_different_submission(self) -> None:
        entries = [_Entry(f"🏛️ {rb.PASSED} plan (1111111111111111) all passed")]

        assert rb.has_passed(entries, "plan", "1111111111111111")
        assert not rb.has_passed(entries, "plan", "2222222222222222")


class TestTheReviewerBrief:
    def test_it_carries_the_lens_and_the_submission(self) -> None:
        seat = rb.Seat(agent="devils-advocate", lens="Argue why this is wrong.")

        brief = rb.review_brief(
            gate="plan", seat=seat, work_order="SEO audit", submission="Crawl everything", tasks="T1 Crawl"
        )

        assert "Argue why this is wrong." in brief
        assert "Crawl everything" in brief
        assert "PASS or FAIL" in brief
