"""System-prompt construction for an agent run."""

from __future__ import annotations

from collections.abc import Sequence

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
    "Do the work. Asking is the exception, not the opening move: if something is "
    "unclear but you could still make progress, state the assumption you are working "
    "under and carry on — a person can correct an assumption, and cannot do anything "
    "with a run that stopped to ask. Use ask_human only when you genuinely cannot "
    "proceed at all, never for something you could look up, and never twice for the "
    "same decision.\n"
    "When you do ask, use ask_human rather than ending your turn with a question in "
    "prose: the run ends when you stop, and a finished run cannot be replied to, so "
    "a question asked that way reaches nobody.\n"
    "You do not self-declare done. Marking a step done requires saying what you produced "
    "and where it is, and the order cannot be finished with nothing attached to it — a "
    "checklist of ticks is not a deliverable. If the request implies something a person "
    "will open, produce it and attach it with attach_document. Writing about the work is "
    "not the work: a document describing how one would do the task does not complete the "
    "task, however good the document is.\n"
    "'blocked' means one specific step cannot proceed and needs someone else — it is "
    "not how you end your turn. Mark only the step that is actually stuck, and say what "
    "would unstick it; leave the steps you simply have not reached as pending. Sweeping "
    "the rest of the list to blocked hides which one needs help and reads to the person "
    "watching as though the whole order has failed. If the obstacle is one your "
    "supervisor could clear — a tool you lack, access you do not have, a call above your "
    "level — escalate instead: that starts a run for them, and they pick the work up "
    "from where you left it."
)


# Plan mode is enforced in the authority gate — these tools are not even offered.
# Saying so up front is still worth it: an agent that discovers the limit by being
# refused spends turns proposing work, and often reports the refusal as a failure
# rather than delivering the plan it was actually asked for.
_PLAN_ONLY_GUIDANCE = (
    "This work order is in PLAN MODE. Work it out before you work on it. You can "
    "read, research, ask questions and delegate planning to your reports; every "
    "write, execution and outbound action is unavailable for now, and your reports "
    "are under the same restriction.\n"
    "Finish by calling submit_plan. That puts your plan in front of a person, and "
    "their approval is what starts the actual work — so this is not a dead end and "
    "the unavailable tools are not a failure to report. Write the task list with "
    "set_work_order_tasks first: the summary explains the plan, the task list is the "
    "plan. If they reject it, revise and submit again."
)


# What each kind can be handed, said from the delegator's side of the chart.
_REPORT_NOTES = {
    "coordinator": "coordinator — cannot act directly, but plans and delegates further down their own branch",
    "operator": "operator — does hands-on work with the tools they hold",
    "advisory": "advisory — researches and recommends; cannot act and cannot delegate onward",
}

# Names before the list truncates. A large org would otherwise push the actual work
# out of the context window with a wall of colleagues.
ROSTER_CAP = 20


def _names(agents: Sequence[Agent]) -> str:
    shown = [a.name for a in agents[:ROSTER_CAP]]
    extra = len(agents) - len(shown)
    return ", ".join(shown) + (f", and {extra} more" if extra > 0 else "")


def _roster_guidance(reports: Sequence[Agent], advisors: Sequence[Agent]) -> str:
    """Name the colleagues an agent can actually route work to.

    Nothing used to tell an agent who its reports were — the roster appeared only
    inside the error you get for naming the wrong one, which an agent has to guess a
    name to see. Seen live: a chief-of-staff told to "route the crawl through the
    engineering chain" worked out that it wanted the technical-project-manager, could
    not name anyone to send it to, and escalated to a human instead — twice — then
    marked the order blocked. It had a delegate_task away from a working route.
    """
    lines: list[str] = []
    if reports:
        lines.append(
            "YOUR DIRECT REPORTS — delegate_task reaches these and only these:\n"
            + "\n".join(f"- {a.name} ({_REPORT_NOTES.get(a.kind, a.kind)})" for a in reports[:ROSTER_CAP])
        )
        if len(reports) > ROSTER_CAP:
            lines.append(f"(…and {len(reports) - ROSTER_CAP} more reports.)")
        # The load-bearing sentence. Without it an agent that needs a skill two levels
        # down concludes it cannot be reached, rather than handing it to the branch
        # that owns it — which is how a delegating org is supposed to work.
        lines.append(
            "If the skill you need sits further down than this list, that is not a dead end: "
            "delegate to the coordinator whose branch owns it and say what you need. Passing it "
            "on is their job, and they can see their own reports the way you see yours."
        )
    if advisors:
        lines.append(f"Advisory agents you can consult_peer anywhere in the org: {_names(advisors)}.")
    return "\n\n".join(lines)


def build_system_prompt(
    agent: Agent,
    *,
    work_order_title: str | None = None,
    plan_only: bool = False,
    reports: Sequence[Agent] | None = None,
    advisors: Sequence[Agent] | None = None,
) -> str:
    """Compose the system prompt from the agent's identity, kind, and persona."""
    name = agent.display_name or agent.name
    parts = [
        f"You are {name}, an AI agent operating inside the KM2 knowledge platform.",
        _KIND_GUIDANCE.get(agent.kind, _KIND_GUIDANCE["operator"]),
    ]
    if agent.persona:
        parts.append(agent.persona.strip())
    roster = _roster_guidance(reports or [], advisors or [])
    if roster:
        parts.append(roster)
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
