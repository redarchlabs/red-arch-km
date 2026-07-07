"""Decision-table evaluation for the ``businessRule`` task.

A decision table derives output values from the run's expression context by
evaluating an ordered list of rules — the natural home for data-derivation
branching (e.g. "amount >= 100 => tier=gold"). Its outputs become run variables,
so a downstream exclusive gateway can route on them.

Pure + sandboxed: conditions are jsonlogic expressions (whitelisted ops, no
attribute access, no eval — see jsonlogic.py), so a table can never execute
arbitrary code. Unit-testable in isolation; the engine owns the step/variable
side effects.
"""

from __future__ import annotations

from typing import Any

from api.services.workflow.jsonlogic import json_logic

HIT_FIRST = "first"
HIT_COLLECT = "collect"


def evaluate_decision_table(spec: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a decision table, returning the merged output dict.

    ``spec = {"hit_policy": "first"|"collect", "rules": [{"when": <jsonlogic>,
    "output": {<var>: <value>}}]}``.

    - ``first`` (default): the first rule whose ``when`` is truthy contributes its
      output, then evaluation stops.
    - ``collect``: every matching rule's output is merged (later rules win on key
      collisions).

    A rule with no ``when`` (or ``when`` is null) always matches — an ``else``/
    default row. A malformed spec or rule is skipped, never raised, so a bad table
    yields ``{}`` rather than sinking the run.
    """
    if not isinstance(spec, dict):
        return {}
    hit_policy = spec.get("hit_policy", HIT_FIRST)
    rules = spec.get("rules")
    if not isinstance(rules, list):
        return {}

    output: dict[str, Any] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        when = rule.get("when")
        if when is not None and not bool(json_logic(when, context)):
            continue
        rule_output = rule.get("output")
        if isinstance(rule_output, dict):
            output.update(rule_output)
        if hit_policy != HIT_COLLECT:
            break
    return output
