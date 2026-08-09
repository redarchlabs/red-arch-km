"""What the model is charged to re-read every turn.

An agent run re-sends its whole transcript on every turn, so a single 12k-character
tool result is not paid once — it is paid again on every remaining turn of the run.
Long runs were spending most of their tokens re-reading their own history.

Compaction shrinks what the model *processes* without losing anything: the full
result is already persisted as a run step, so the transcript keeps a preview and a
handle, and ``read_run_detail`` fetches the rest on the rare turn that needs it.

The invariant every test here defends: compaction may only ever change the
transcript. The stored record is the permanent one and must stay whole.
"""

from __future__ import annotations

import json

import pytest
from api.services.agents.transcript import (
    DETAIL_TOOL,
    compact_tool_output,
    fold_old_turns,
    transcript_chars,
)

pytestmark = pytest.mark.unit


def _big(n: int) -> str:
    return "x" * n


class TestCompactingOneToolResult:
    def test_a_small_result_is_left_exactly_alone(self) -> None:
        output = {"status": "ok", "days": 12}

        compacted, elided = compact_tool_output(output, "call_1", budget=1000)

        assert compacted == output
        assert elided is False

    def test_an_oversized_field_is_replaced_by_a_preview_and_a_handle(self) -> None:
        output = {"status": "ok", "result": _big(5000)}

        compacted, elided = compact_tool_output(output, "call_1", budget=500)

        assert elided is True
        # The small field survives: the model still knows the call succeeded.
        assert compacted["status"] == "ok"
        assert compacted["result"]["elided"] is True
        assert compacted["result"]["chars"] == 5000
        assert compacted["result"]["preview"].startswith("xxx")
        assert len(json.dumps(compacted)) <= 500

    def test_it_says_how_to_get_the_rest(self) -> None:
        # A model that cannot see the way back to the detail will either guess or
        # re-run the tool; both cost more than the elision saved.
        compacted, _ = compact_tool_output({"result": _big(5000)}, "call_7", budget=500)

        detail = compacted["_detail"]
        assert detail["call_id"] == "call_7"
        assert detail["tool"] == DETAIL_TOOL

    def test_the_largest_field_goes_first(self) -> None:
        output = {"small": "a" * 50, "huge": _big(5000)}

        compacted, _ = compact_tool_output(output, "c", budget=400)

        assert compacted["small"] == "a" * 50
        assert compacted["huge"]["elided"] is True

    def test_it_keeps_eliding_until_the_budget_is_met(self) -> None:
        output = {"a": _big(3000), "b": _big(3000)}

        compacted, _ = compact_tool_output(output, "c", budget=400)

        assert compacted["a"]["elided"] is True
        assert compacted["b"]["elided"] is True
        assert len(json.dumps(compacted)) <= 400

    def test_a_non_positive_budget_disables_elision(self) -> None:
        # The console uses this: it streams events to a watching human instead of
        # persisting run steps, so there would be nothing for read_run_detail to
        # find. Eliding what nothing recorded is losing it, not compacting it.
        output = {"result": _big(5000)}

        compacted, elided = compact_tool_output(output, "c", budget=0)

        assert compacted == output
        assert elided is False

    def test_a_non_dict_result_is_still_bounded(self) -> None:
        # Handlers return dicts by contract, but the transcript must not blow up
        # if one ever doesn't.
        compacted, elided = compact_tool_output(_big(5000), "c", budget=400)  # type: ignore[arg-type]

        assert elided is True
        assert len(json.dumps(compacted)) <= 400


class TestFoldingOldTurns:
    def _messages(self) -> list[dict]:
        def call(cid: str) -> dict:
            return {"id": cid, "type": "function", "function": {"name": "f", "arguments": "{}"}}

        return [
            {"role": "system", "content": "You are a payroll agent."},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "turn one", "tool_calls": [call("c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": "result one"},
            {"role": "assistant", "content": "turn two", "tool_calls": [call("c2")]},
            {"role": "tool", "tool_call_id": "c2", "content": "result two"},
            {"role": "assistant", "content": "turn three"},
        ]

    def test_the_system_prompt_and_the_task_always_survive(self) -> None:
        # Folding these away would leave the agent without its instructions or its
        # objective — the run would wander rather than shrink.
        folded = fold_old_turns(self._messages(), "Earlier: looked up two records.", keep_recent=2)

        assert folded[0]["role"] == "system"
        assert folded[1] == {"role": "user", "content": "task"}

    def test_the_middle_becomes_one_summary(self) -> None:
        folded = fold_old_turns(self._messages(), "Earlier: looked up two records.", keep_recent=2)

        summary = folded[2]
        assert summary["role"] == "system"
        assert "Earlier: looked up two records." in summary["content"]

    def test_the_most_recent_turns_are_kept_verbatim(self) -> None:
        folded = fold_old_turns(self._messages(), "summary", keep_recent=2)

        # The window widened back to "turn two" so its result is not orphaned —
        # keep_recent is a floor, not a ceiling.
        assert folded[-3:] == self._messages()[-3:]

    def test_a_short_transcript_is_returned_untouched(self) -> None:
        short = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"}]

        assert fold_old_turns(short, "summary", keep_recent=4) == short

    def test_a_fold_never_orphans_a_tool_result_from_its_call(self) -> None:
        # A tool message whose assistant tool_call was folded away is a message the
        # API rejects: tool_call_id must refer to a call in the transcript.
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "t"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c9", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c9", "content": "r"},
        ]

        folded = fold_old_turns(messages, "summary", keep_recent=1)

        kept_ids = {m["tool_call_id"] for m in folded if m["role"] == "tool"}
        called_ids = {c["id"] for m in folded if m.get("tool_calls") for c in m["tool_calls"]}
        assert kept_ids <= called_ids


class TestMeasuring:
    def test_it_counts_the_whole_serialized_transcript(self) -> None:
        messages = [{"role": "user", "content": "hello"}]

        assert transcript_chars(messages) == len(json.dumps(messages, default=str))
