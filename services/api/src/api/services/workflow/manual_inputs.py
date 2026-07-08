"""Declared input variables for a manual (BPMN "none" start event) workflow.

A manual trigger node declares the variables its workflow accepts under
``data.inputs`` — a list of ``{key, label, type, required}`` specs. When such a
workflow is run on demand, the caller supplies values for those variables; this
module validates + coerces the raw payload against the declared schema so the
run's ``inputs`` context is well-typed and can't carry undeclared smuggled keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

INPUT_TYPES = ("text", "number", "boolean")

# A key must be resolvable by the action template regex (``{{ inputs.<key> }}``)
# and JsonLogic paths, so it is constrained to the same charset the UI slugifies
# to. A non-conforming key would silently never render; reject it at declaration.
_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


class InputValidationError(ValueError):
    """A required input is missing or a value can't be coerced to its type."""


@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    type: str
    required: bool


def _trigger_data(definition: dict[str, Any]) -> dict[str, Any]:
    for node in definition.get("nodes", []):
        if node.get("type") == "trigger":
            return node.get("data") or {}
    return {}


def is_manual_trigger(definition: dict[str, Any]) -> bool:
    """True when the workflow's start is a BPMN "none" (manual, on-demand) event —
    i.e. it runs with caller-supplied input variables rather than a record change.
    Keyed on the trigger's ``source``, so an entity-bound workflow whose trigger is
    switched to manual is also on-demand."""
    return _trigger_data(definition).get("source") == "manual"


def declared_inputs(definition: dict[str, Any]) -> list[InputSpec]:
    """The input variables the workflow's manual trigger declares, in order.

    Lenient: malformed/keyless entries are skipped and an unknown ``type`` falls
    back to ``text`` — a bad definition yields fewer inputs, never a crash."""
    specs: list[InputSpec] = []
    seen: set[str] = set()
    for item in _trigger_data(definition).get("inputs") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key or key in seen or not _KEY_RE.match(key):
            continue
        seen.add(key)
        typ = item.get("type") if item.get("type") in INPUT_TYPES else "text"
        specs.append(
            InputSpec(
                key=key,
                label=str(item.get("label") or key),
                type=str(typ),
                required=bool(item.get("required", False)),
            )
        )
    return specs


def _coerce_value(spec: InputSpec, value: Any) -> Any:
    if spec.type == "number":
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise InputValidationError(f"input {spec.label!r} must be a number") from None
        return int(num) if num.is_integer() else num
    if spec.type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    return "" if value is None else str(value)


def coerce_inputs(definition: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + coerce ``raw`` against the trigger's declared inputs.

    Only declared keys survive (a caller can't inject undeclared variables); each
    value is coerced to its declared type; a missing/blank *required* input raises
    :class:`InputValidationError` (the router turns this into a 422).
    """
    result: dict[str, Any] = {}
    for spec in declared_inputs(definition):
        present = spec.key in raw and raw[spec.key] not in (None, "")
        if not present:
            if spec.required:
                raise InputValidationError(f"missing required input: {spec.label!r}")
            continue
        result[spec.key] = _coerce_value(spec, raw[spec.key])
    return result
