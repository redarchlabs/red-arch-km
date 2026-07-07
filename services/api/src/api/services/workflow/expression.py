"""Data-transform evaluation for the ``script`` task (Tier 1: JSON mapping).

A script/transform task maps output variable names to jsonlogic expressions
evaluated against the run context — a declarative, sandboxed alternative to
arbitrary code (there is deliberately NO Turing-complete execution language:
jsonlogic is whitelisted-ops, no attribute access, no eval — see jsonlogic.py).
The outputs become run variables, so later steps/gateways can use them.

A future Tier 2 (CEL) can slot in behind this same ``evaluate_transform``
interface when richer expressions are needed. Pure + side-effect-free so it is
trivially unit-testable; the engine owns the step/variable writes.
"""

from __future__ import annotations

from typing import Any

from api.services.workflow.jsonlogic import json_logic


def evaluate_transform(mapping: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a ``{out_var: expr}`` mapping, returning the computed outputs.

    Each value is either a jsonlogic expression (a dict/list — evaluated against
    ``context``) or a literal (str/int/float/bool/None — passed through). A
    malformed mapping yields ``{}`` rather than raising, so a bad transform can't
    sink the run.
    """
    if not isinstance(mapping, dict):
        return {}
    outputs: dict[str, Any] = {}
    for key, expr in mapping.items():
        if not isinstance(key, str):
            continue
        outputs[key] = json_logic(expr, context) if isinstance(expr, (dict, list)) else expr
    return outputs
