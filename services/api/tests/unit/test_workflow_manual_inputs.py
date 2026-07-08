"""Unit tests for manual-trigger input declaration + coercion."""

from __future__ import annotations

import pytest
from api.services.workflow.manual_inputs import (
    InputValidationError,
    coerce_inputs,
    declared_inputs,
    is_manual_trigger,
)

pytestmark = pytest.mark.unit


class TestIsManualTrigger:
    def test_true_for_manual_source(self) -> None:
        assert is_manual_trigger(_def([{"key": "x"}])) is True

    def test_false_for_data_change_trigger(self) -> None:
        d = {"nodes": [{"id": "t", "type": "trigger", "data": {"operations": ["update"]}}], "edges": []}
        assert is_manual_trigger(d) is False

    def test_false_when_no_trigger(self) -> None:
        assert is_manual_trigger({"nodes": [], "edges": []}) is False


def _def(inputs: list[dict] | None) -> dict:
    data = {"source": "manual"}
    if inputs is not None:
        data["inputs"] = inputs
    return {"nodes": [{"id": "t", "type": "trigger", "data": data}], "edges": []}


class TestDeclaredInputs:
    def test_reads_specs_in_order(self) -> None:
        specs = declared_inputs(
            _def(
                [
                    {"key": "email", "label": "Email", "type": "text", "required": True},
                    {"key": "amount", "label": "Amount", "type": "number"},
                ]
            )
        )
        assert [(s.key, s.type, s.required) for s in specs] == [
            ("email", "text", True),
            ("amount", "number", False),
        ]

    def test_skips_keyless_and_dedupes(self) -> None:
        specs = declared_inputs(
            _def([{"label": "no key"}, {"key": "a"}, {"key": "a", "label": "dup"}, "junk"])
        )
        assert [s.key for s in specs] == ["a"]

    def test_unknown_type_falls_back_to_text(self) -> None:
        (spec,) = declared_inputs(_def([{"key": "x", "type": "wat"}]))
        assert spec.type == "text"

    def test_rejects_non_template_safe_key(self) -> None:
        # A key with chars outside the template regex would silently never render,
        # so it must not be declared at all.
        specs = declared_inputs(_def([{"key": "bad-key!"}, {"key": "good_1"}]))
        assert [s.key for s in specs] == ["good_1"]

    def test_no_trigger_or_no_inputs(self) -> None:
        assert declared_inputs({"nodes": [], "edges": []}) == []
        assert declared_inputs(_def(None)) == []


class TestCoerceInputs:
    def test_coerces_by_type(self) -> None:
        definition = _def(
            [
                {"key": "email", "type": "text"},
                {"key": "count", "type": "number"},
                {"key": "flag", "type": "boolean"},
            ]
        )
        out = coerce_inputs(definition, {"email": "a@b.com", "count": "3", "flag": "yes"})
        assert out == {"email": "a@b.com", "count": 3, "flag": True}

    def test_number_keeps_float(self) -> None:
        out = coerce_inputs(_def([{"key": "n", "type": "number"}]), {"n": "2.5"})
        assert out == {"n": 2.5}

    def test_bad_number_raises(self) -> None:
        with pytest.raises(InputValidationError):
            coerce_inputs(_def([{"key": "n", "type": "number"}]), {"n": "abc"})

    def test_drops_undeclared_keys(self) -> None:
        # A caller cannot smuggle variables the workflow never declared.
        out = coerce_inputs(_def([{"key": "a", "type": "text"}]), {"a": "1", "evil": "x"})
        assert out == {"a": "1"}

    def test_missing_required_raises(self) -> None:
        with pytest.raises(InputValidationError):
            coerce_inputs(_def([{"key": "a", "type": "text", "required": True}]), {})

    def test_blank_required_raises(self) -> None:
        with pytest.raises(InputValidationError):
            coerce_inputs(_def([{"key": "a", "type": "text", "required": True}]), {"a": ""})

    def test_optional_missing_is_omitted(self) -> None:
        out = coerce_inputs(_def([{"key": "a", "type": "text"}]), {})
        assert out == {}

    def test_required_falsey_values_are_present(self) -> None:
        # 0 and False are legitimate values, not "missing".
        definition = _def(
            [
                {"key": "n", "type": "number", "required": True},
                {"key": "b", "type": "boolean", "required": True},
            ]
        )
        assert coerce_inputs(definition, {"n": 0, "b": False}) == {"n": 0, "b": False}
