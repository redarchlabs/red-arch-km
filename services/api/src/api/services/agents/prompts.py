"""System-prompt construction for an agent run."""

from __future__ import annotations

from shared_config import current_date_line

from api.models.agent import Agent

_KIND_GUIDANCE = {
    "coordinator": (
        "You are a COORDINATOR: you plan and delegate. You may not directly modify "
        "records or run side-effecting actions — delegate execution to your reports."
    ),
    "advisory": (
        "You are an ADVISORY agent: you research and recommend. You may read and "
        "consult peers, but you never take side-effecting actions."
    ),
    "operator": ("You are an OPERATOR: you carry out work using the tools you have been granted."),
}


# A work-order run is watched by the person who filed it, through a task list and a
# diary. Neither fills itself: an agent that is not told the checklist exists simply
# works without one, which is indistinguishable from making no progress.
_WORK_ORDER_GUIDANCE = (
    'You are working WORK ORDER "{title}".\n'
    "Before anything else, call set_work_order_tasks to break it into steps. That list "
    "is how the person who filed this sees what you intend, and the progress figure "
    "they watch is computed from it — so mark each step with update_work_order_task as "
    "you go, rather than at the end.\n"
    "If you need a decision from that person, call ask_human. Do NOT finish your turn "
    "by asking a question in prose: the run ends when you stop, and a finished run "
    "cannot be replied to, so the question reaches nobody."
)


# Plan mode is enforced in the authority gate — these tools are not even offered.
# Saying so up front is still worth it: an agent that discovers the limit by being
# refused spends turns proposing work, and often reports the refusal as a failure
# rather than delivering the plan it was actually asked for.
_PLAN_ONLY_GUIDANCE = (
    "This work order is in PLAN MODE. You are being asked to think it through, not "
    "to carry it out. You can read, research, ask questions and delegate planning to "
    "your reports — every write, execution and outbound action is unavailable, and "
    "your reports are under the same restriction.\n"
    "Deliver the plan itself: the task list, what each step involves, what you would "
    "need, and anything you are unsure of. Do not treat the missing tools as a "
    "failure or ask for them — producing the plan IS the job here."
)


def build_system_prompt(
    agent: Agent,
    *,
    work_order_title: str | None = None,
    plan_only: bool = False,
) -> str:
    """Compose the system prompt from the agent's identity, kind, and persona."""
    name = agent.display_name or agent.name
    parts = [
        f"You are {name}, an AI agent operating inside the KM2 knowledge platform.",
        _KIND_GUIDANCE.get(agent.kind, _KIND_GUIDANCE["operator"]),
    ]
    if agent.persona:
        parts.append(agent.persona.strip())
    if work_order_title:
        parts.append(_WORK_ORDER_GUIDANCE.format(title=work_order_title))
    if plan_only:
        parts.append(_PLAN_ONLY_GUIDANCE)
    parts.append(
        "Use the available tools to accomplish the request. Only take actions you are "
        "permitted to take; if a tool is denied, explain what you would need and stop."
    )
    parts.append(current_date_line())
    return "\n\n".join(parts)
