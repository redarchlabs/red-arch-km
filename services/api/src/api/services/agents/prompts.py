"""System-prompt construction for an agent run."""

from __future__ import annotations

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
    "operator": (
        "You are an OPERATOR: you carry out work using the tools you have been granted."
    ),
}


def build_system_prompt(agent: Agent) -> str:
    """Compose the system prompt from the agent's identity, kind, and persona."""
    name = agent.display_name or agent.name
    parts = [
        f"You are {name}, an AI agent operating inside the KM2 knowledge platform.",
        _KIND_GUIDANCE.get(agent.kind, _KIND_GUIDANCE["operator"]),
    ]
    if agent.persona:
        parts.append(agent.persona.strip())
    parts.append(
        "Use the available tools to accomplish the request. Only take actions you are "
        "permitted to take; if a tool is denied, explain what you would need and stop."
    )
    return "\n\n".join(parts)
