"""Schema coverage for per-row conditional rendering on ``record_list``.

``row_lookups`` + ``row_workflow_visible_when`` let a board hide a row's action
based on ANOTHER entity's records (the catalog's Enroll hides for courses the
caller is already enrolled in). The client evaluates the rule; this pins the
config contract: the fields round-trip, lookups validate their shape, and a
malformed lookup is rejected rather than silently dropped.
"""

from __future__ import annotations

import pytest
from api.schemas.form_elements import RecordListElement, RecordListLookup
from pydantic import ValidationError

ENROLL_RULE = {"!": {"in": [{"var": "id"}, {"var": "lookups.my_course_ids"}]}}


def _catalog_element(**overrides):
    base = {
        "id": "list",
        "type": "record_list",
        "entity": "course",
        "row_workflow_id": "dcf8eb37-d823-482d-ad0b-b030ed86404b",
        "row_action_label": "Enroll",
        "row_lookups": [
            {
                "key": "my_course_ids",
                "entity": "enrollment",
                "filters": [{"field": "learner", "op": "eq", "value": "@me"}],
                "pluck": "course",
            }
        ],
        "row_workflow_visible_when": ENROLL_RULE,
        "row_workflow_hidden_text": "Enrolled ✓",
    }
    base.update(overrides)
    return base


def test_lookup_and_visibility_fields_round_trip() -> None:
    el = RecordListElement.model_validate(_catalog_element())
    dumped = el.model_dump(mode="json")
    assert dumped["row_lookups"][0]["key"] == "my_course_ids"
    assert dumped["row_lookups"][0]["pluck"] == "course"
    assert dumped["row_lookups"][0]["filters"][0]["value"] == "@me"
    assert dumped["row_workflow_visible_when"] == ENROLL_RULE
    assert dumped["row_workflow_hidden_text"] == "Enrolled ✓"


def test_fields_default_to_absent() -> None:
    el = RecordListElement.model_validate({"id": "l", "type": "record_list", "entity": "course"})
    assert el.row_lookups == []
    assert el.row_workflow_visible_when is None
    assert el.row_workflow_hidden_text is None
    assert el.row_link_visible_when is None


@pytest.mark.parametrize(
    "bad",
    [
        {"entity": "enrollment", "pluck": "course"},  # missing key
        {"key": "My Courses", "entity": "enrollment", "pluck": "course"},  # bad key charset
        {"key": "k", "entity": "enrollment"},  # missing pluck
        {"key": "k", "entity": "enrollment", "pluck": "course", "extra": True},  # unknown field
    ],
)
def test_malformed_lookup_is_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        RecordListLookup.model_validate(bad)
