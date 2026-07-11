"""Role-class tool gate — the KM2 analog of the reference kind-gate.

Every agent has a governance ``kind``; each kind may only use certain tool
categories. This is a *hard* restriction enforced in code before grants/approval
are even consulted, so a coordinator can never directly mutate and an advisory
agent can never take a side-effecting action, no matter its grants.

* coordinator — reads, plans, delegates, escalates; must delegate all execution.
* advisory   — reads + consults/escalates only; produces recommendations, never mutates.
* operator   — full tool surface (still subject to grants + approval).
"""

from __future__ import annotations

from api.services.agents.tools.spec import Category, ToolSpec

_ALLOWED_CATEGORIES: dict[str, frozenset[str]] = {
    "coordinator": frozenset({Category.READ, Category.PLAN, Category.DELEGATE, Category.ESCALATE}),
    "advisory": frozenset({Category.READ, Category.ESCALATE}),
    "operator": frozenset(
        {
            Category.READ,
            Category.WRITE,
            Category.EXECUTE,
            Category.DELEGATE,
            Category.ESCALATE,
            Category.PLAN,
        }
    ),
}


def kind_gate(kind: str, spec: ToolSpec) -> str | None:
    """Return a denial reason if ``kind`` may not use ``spec``'s category, else None."""
    allowed = _ALLOWED_CATEGORIES.get(kind, _ALLOWED_CATEGORIES["operator"])
    if spec.category not in allowed:
        return f"role '{kind}' may not use {spec.category} tools"
    return None
