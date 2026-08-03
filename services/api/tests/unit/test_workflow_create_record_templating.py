"""Unit tests: create_record resolves values the same way update_record does.

create_record used to resolve only ``$ref`` envelopes, so a ``{{ inputs.x }}`` value
was written to the database as that literal string. It failed silently — the record
was created, the run succeeded, and the wrong text only surfaced later on a screen.
"""

from __future__ import annotations

import pytest
from api.services.workflow.actions import _resolve_value_map, _resolve_values

pytestmark = pytest.mark.unit


CONTEXT = {
    "inputs": {"class_name": "Sunday Class", "week": 32},
    "vars": {"q": {"points": 15}},
    "after": {"title": "Esther"},
}


def test_template_string_is_rendered():
    """The regression: this used to come back as the literal '{{ inputs.class_name }}'."""
    out = _resolve_value_map({"session_name": "{{inputs.class_name}}"}, CONTEXT)
    assert out == {"session_name": "Sunday Class"}


def test_ref_envelope_preserves_type():
    """Numbers must stay numbers — a stringified int breaks integer-column filters."""
    out = _resolve_value_map({"points": {"$ref": "vars.q.points"}}, CONTEXT)
    assert out == {"points": 15}
    assert isinstance(out["points"], int)


def test_literals_pass_through_untouched():
    out = _resolve_value_map({"status": "in_progress", "score": 0, "flag": True}, CONTEXT)
    assert out == {"status": "in_progress", "score": 0, "flag": True}


def test_template_can_mix_text_and_tokens():
    out = _resolve_value_map({"label": "Week {{inputs.week}} - {{after.title}}"}, CONTEXT)
    assert out == {"label": "Week 32 - Esther"}


def test_unknown_token_renders_empty_rather_than_raising():
    out = _resolve_value_map({"who": "{{inputs.missing}}"}, CONTEXT)
    assert out == {"who": ""}


def test_old_helper_still_leaves_templates_alone():
    """_resolve_values is still used where templates are deliberately NOT supported;
    this pins the difference so the two helpers are not accidentally merged."""
    assert _resolve_values({"a": "{{inputs.class_name}}"}, CONTEXT) == {"a": "{{inputs.class_name}}"}
    assert _resolve_values({"a": {"$ref": "inputs.week"}}, CONTEXT) == {"a": 32}
