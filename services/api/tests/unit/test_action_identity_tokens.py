"""An action can template the identity of the step running it.

``{{run.id}}`` / ``{{node.id}}`` exist for one reason: they are the only tokens
that are STABLE across a retry of a node and UNIQUE across runs, which is what
an idempotency key needs. A non-idempotent downstream — the robot's ``/say``
and ``/perform`` actually speak — can then tell a genuine second request from a
retry of one whose response was lost, and decline to say the line twice.
"""

from __future__ import annotations

import uuid

from api.services.workflow.actions import ActionContext, _resolve_value_map, _trigger_context

RUN = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _ctx(**kw) -> ActionContext:
    return ActionContext(
        org_id=RUN,
        record_id=None,
        before=None,
        after=None,
        config={},
        trigger_repo=None,
        repo_for_slug=None,
        **kw,
    )


def test_context_carries_run_and_node_identity() -> None:
    ctx = _trigger_context(_ctx(run_id=RUN, node_id="joinsay"))
    assert ctx["run"]["id"] == str(RUN)
    assert ctx["node"]["id"] == "joinsay"


def test_body_templates_an_idempotency_key() -> None:
    body = {"text": "hi", "request_id": "{{run.id}}:{{node.id}}"}
    rendered = _resolve_value_map(body, _trigger_context(_ctx(run_id=RUN, node_id="joinsay")))
    assert rendered["request_id"] == f"{RUN}:joinsay"
    assert rendered["text"] == "hi"


def test_whitespace_inside_the_token_is_tolerated() -> None:
    rendered = _resolve_value_map({"k": "{{ run.id }}:{{ node.id }}"}, _trigger_context(_ctx(run_id=RUN, node_id="n1")))
    assert rendered["k"] == f"{RUN}:n1"


def test_key_is_stable_across_attempts_but_differs_per_node_and_run() -> None:
    """The whole contract in one assertion set: a retry of the same node in the
    same run re-templates to the SAME key (so it deduplicates), while a different
    node, or the same node in a later run, does not (so it still speaks)."""

    def key(run_id, node_id):
        return _resolve_value_map(
            {"k": "{{run.id}}:{{node.id}}"}, _trigger_context(_ctx(run_id=run_id, node_id=node_id))
        )["k"]

    assert key(RUN, "joinsay") == key(RUN, "joinsay")  # retry → same key
    assert key(RUN, "joinsay") != key(RUN, "joingo")  # sibling node → different
    assert key(RUN, "joinsay") != key(uuid.uuid4(), "joinsay")  # next run → different


def test_absent_identity_renders_a_key_too_short_to_deduplicate_on() -> None:
    """Callers that never set identity (dry-run/test paths) render a stub. The
    robot ignores keys this short precisely so a stub cannot silence unrelated
    speech — see app/replay.py MIN_KEY_LENGTH in the robot repo."""
    rendered = _resolve_value_map({"k": "{{run.id}}:{{node.id}}"}, _trigger_context(_ctx()))
    assert rendered["k"] == ":"
    assert len(rendered["k"]) < 8


def test_existing_tokens_are_unaffected() -> None:
    ctx = _trigger_context(_ctx(run_id=RUN, node_id="n1", vars={"kb": {"answer": "42"}}))
    assert _resolve_value_map({"k": "{{vars.kb.answer}}"}, ctx)["k"] == "42"
