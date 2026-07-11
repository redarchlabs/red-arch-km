"""Authority decision engine — the KM2 port of the reference policy tiers.

Precedence, highest first: **deny > ask > allow > (default deny)**.

1. The kind-gate (hard role restriction) can DENY outright.
2. Availability: read tools + role-provided coordination tools are always
   available; execute/write tools must be listed in the agent's grants (write also
   needs the ``records_write`` flag). Anything else is DENY (default-deny).
3. Approval: an available tool named in ``grants.approval_required`` returns ASK —
   the "ask" tier, which the runtime never auto-promotes. Otherwise ALLOW.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from api.models.agent import Agent
from api.services.agents.kind_gate import kind_gate
from api.services.agents.tools.spec import Category, ToolSpec


class Decision(enum.Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class AuthorityVerdict:
    decision: Decision
    reason: str = ""


_ROLE_PROVIDED = frozenset({Category.DELEGATE, Category.ESCALATE, Category.PLAN})


def _is_available(agent: Agent, spec: ToolSpec) -> bool:
    grants = agent.grants or {}
    if spec.always_allowed:
        return True
    if spec.category in _ROLE_PROVIDED:
        # Coordination tools are provided by role; the kind-gate already decided
        # whether this kind may use them.
        return True
    allowed = set(grants.get("tools") or [])
    if spec.category == Category.WRITE:
        return spec.name in allowed and bool(grants.get("records_write"))
    return spec.name in allowed


def decide(agent: Agent, spec: ToolSpec) -> AuthorityVerdict:
    """Resolve whether ``agent`` may invoke ``spec`` — allow, ask, or deny."""
    denial = kind_gate(agent.kind, spec)
    if denial:
        return AuthorityVerdict(Decision.DENY, denial)
    if not _is_available(agent, spec):
        return AuthorityVerdict(Decision.DENY, f"'{spec.name}' is not granted to this agent")
    approval = set((agent.grants or {}).get("approval_required") or [])
    if spec.name in approval:
        return AuthorityVerdict(Decision.ASK, "requires human approval")
    return AuthorityVerdict(Decision.ALLOW)


def available_tools(agent: Agent, specs: list[ToolSpec]) -> list[ToolSpec]:
    """Subset of ``specs`` the agent may see (ALLOW or ASK — not DENY).

    ASK tools are offered to the model; the gate fires at call time so the model
    can still *propose* an action a human then approves.
    """
    return [s for s in specs if decide(agent, s).decision is not Decision.DENY]
