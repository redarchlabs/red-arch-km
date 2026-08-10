"""A ``select`` whose choices are records, not a list the author typed.

A static ``options`` list is a copy of the data, and copies go stale. The
instructor console that starts a robot lesson listed one week's lesson, so
adding the next week meant editing the view — the picker was the reason the
whole console read as single-week even though the workflow behind it was
already generic.
"""

from __future__ import annotations

import pytest
from api.schemas.form_elements import InputElement
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _select(**source):
    return InputElement(id="wk", key="week_number", control="select", options_from=source)


class TestDeclaringAnEntitySource:
    def test_entity_and_value_are_enough(self) -> None:
        el = _select(entity="lesson", value="week_number")

        assert el.options_from is not None
        assert el.options_from.entity == "lesson"
        assert el.options_from.label is None  # renderer falls back to the value field

    def test_a_label_field_is_carried_through(self) -> None:
        el = _select(entity="lesson", value="week_number", label="title")

        assert el.options_from is not None
        assert el.options_from.label == "title"

    def test_filters_narrow_the_choices(self) -> None:
        """How a picker offers only what is ready to pick — a lesson still in
        draft should not be startable in front of a class."""
        el = _select(
            entity="lesson",
            value="week_number",
            filters=[{"field": "status", "op": "eq", "value": "ready"}],
        )

        assert el.options_from is not None
        assert [(f.field, f.op, f.value) for f in el.options_from.filters] == [("status", "eq", "ready")]

    def test_sort_defaults_to_ascending(self) -> None:
        """Unlike a record_list status board, which wants newest-first, a picker
        reads as an ordered menu — week 1 before week 33."""
        el = _select(entity="lesson", value="week_number")

        assert el.options_from is not None
        assert el.options_from.sort_dir == "asc"

    def test_an_unknown_key_is_rejected(self) -> None:
        """extra=forbid across the layout schema: a typo is a 422, never silently
        stored and silently ignored at render time."""
        with pytest.raises(ValidationError):
            _select(entity="lesson", value="week_number", labell="title")


class TestTheDefaultStaysAStaticList:
    def test_an_input_without_a_source_is_unchanged(self) -> None:
        el = InputElement(
            id="x",
            key="k",
            control="select",
            options=[{"value": "32"}, {"value": "33"}],
        )

        assert el.options_from is None
        assert [o.value for o in el.options] == ["32", "33"]

    def test_a_plain_text_input_still_parses(self) -> None:
        el = InputElement(id="c", key="class_name", control="text", default="Sunday Class")

        assert el.options_from is None
        assert el.default == "Sunday Class"
