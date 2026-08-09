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
from typing import Any

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


class Posture:
    """Per-run postures, layered over the org's ``agent_autonomy``.

    A work order carries one of these (see ``WORK_ORDER_MODES``); anything without
    a work order runs on the org posture as before.
    """

    PLAN_ONLY = "plan"  # think it through; change nothing
    AUTOMATIC = "automatic"  # approvals granted without asking


# What a plan-only run may still do. It keeps DELEGATE deliberately: a coordinator
# whose entire job is delegating produces a thin plan alone, and a delegated child
# inherits the work order — and therefore this posture — so nothing it reaches can
# act either.
_PLAN_ONLY_CATEGORIES = frozenset({Category.READ, Category.PLAN, Category.DELEGATE, Category.ESCALATE})


def _granted(allowed: set[str], name: str) -> bool:
    """Is tool ``name`` covered by the agent's ``grants.tools``?

    Exact match, or — for dynamically-named MCP tools ``mcp__<server>__<tool>`` —
    a server wildcard ``mcp__<server>__*`` (or the catch-all ``mcp__*``). Wildcards
    let the roster pre-authorize a whole MCP server before it is connected (its
    exact tool names aren't known until the server lists them)."""
    if name in allowed:
        return True
    if name.startswith("mcp__"):
        server_wildcard = name.rsplit("__", 1)[0] + "__*"  # mcp__<server>__*
        return server_wildcard in allowed or "mcp__*" in allowed
    return False


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
        return _granted(allowed, spec.name) and bool(grants.get("records_write"))
    return _granted(allowed, spec.name)


def decide(agent: Agent, spec: ToolSpec, *, autonomy: str = "high_touch") -> AuthorityVerdict:
    """Resolve whether ``agent`` may invoke ``spec`` — allow, ask, or deny.

    ``autonomy`` is the org-level posture (``high_touch`` | ``balanced`` |
    ``hands_off``). Under **high_touch**, any *side-effecting* tool (an action that
    leaves the company — sending email, posting to Slack, running an external MCP
    tool) is forced to ASK even if it is not listed per-agent in
    ``approval_required``, so a single human gates every outbound action without
    each agent having to enumerate them. Internal writes (record/document tools,
    ``side_effecting=False``) are never forced — they stay ALLOW.
    """
    denial = kind_gate(agent.kind, spec)
    if denial:
        return AuthorityVerdict(Decision.DENY, denial)
    if autonomy == Posture.PLAN_ONLY and (spec.category not in _PLAN_ONLY_CATEGORIES or spec.side_effecting):
        # Not a dead end: the way out of plan mode is to finish the plan and submit
        # it, which is the tool this posture adds. An agent that is only refused
        # reports the refusal as a failure instead of delivering the plan.
        return AuthorityVerdict(
            Decision.DENY,
            f"'{spec.name}' is unavailable while this work order is in plan mode. "
            "Finish your research and call submit_plan — the person approving that "
            "plan is what lets the work start.",
        )
    if not _is_available(agent, spec):
        return AuthorityVerdict(Decision.DENY, f"'{spec.name}' is not granted to this agent")
    if spec.always_ask:
        # Before the AUTOMATIC short-circuit: a tool whose purpose is to put a
        # decision in front of a person cannot be auto-approved without becoming
        # a no-op.
        return AuthorityVerdict(Decision.ASK, "a person decides this one")
    if autonomy == Posture.AUTOMATIC:
        # Grants and the kind-gate still hold — this only removes the human from
        # the loop on tools the agent was already allowed to use.
        return AuthorityVerdict(Decision.ALLOW)
    approval = set((agent.grants or {}).get("approval_required") or [])
    if spec.name in approval:
        return AuthorityVerdict(Decision.ASK, "requires human approval")
    if autonomy == "high_touch" and spec.side_effecting:
        return AuthorityVerdict(Decision.ASK, "high-touch: external action requires human approval")
    return AuthorityVerdict(Decision.ALLOW)


def available_tools(agent: Agent, specs: list[ToolSpec], *, autonomy: str = "high_touch") -> list[ToolSpec]:
    """Subset of ``specs`` the agent may see (ALLOW or ASK — not DENY).

    ASK tools are offered to the model; the gate fires at call time so the model
    can still *propose* an action a human then approves. ``autonomy`` matters here
    as well as at call time: a plan-only run offered tools it will be denied burns
    turns proposing actions that can never happen.
    """
    return [s for s in specs if decide(agent, s, autonomy=autonomy).decision is not Decision.DENY]


def posture_for(work_order: Any, org_posture: str) -> str:
    """The posture a run executes under: the work order's mode wins where it has one.

    ``manual`` defers to the org so an org that has moved off ``high_touch`` keeps
    that setting; the other two modes are explicit per-job choices and override it.
    """
    mode = getattr(work_order, "mode", None) if work_order is not None else None
    if mode == "plan":
        return Posture.PLAN_ONLY
    if mode == "automatic":
        return Posture.AUTOMATIC
    return org_posture
