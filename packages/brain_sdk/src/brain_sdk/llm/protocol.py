"""Provider-agnostic LLM client interface.

The knowledge engine talks to LLMs through this narrow protocol so extraction,
entity adjudication, and the agentic chat loop can run on OpenAI (default),
Anthropic, Gemini, or any future provider via configuration. Providers live in
sibling modules and are constructed by :func:`brain_sdk.llm.factory.make_llm_client`.

This slice covers text + JSON completion (what extraction/resolution need).
Tool-use — required by the agentic query loop — extends this protocol in a
later slice via :meth:`LLMClient.complete_tools`, kept optional here so
text-only providers remain conformant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """A single chat message in provider-neutral form."""

    role: str  # "system" | "user" | "assistant"
    content: str


@runtime_checkable
class LLMClient(Protocol):
    """Minimal, provider-neutral chat interface."""

    @property
    def model(self) -> str:
        """The model id this client is configured for."""
        ...

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_object: bool = False,
    ) -> str:
        """Return the assistant's text.

        ``json_object=True`` requests a strict JSON object response where the
        provider supports it (callers must still parse + validate defensively).
        """
        ...


def as_provider_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Render neutral messages to the ``{role, content}`` dicts most SDKs accept."""
    return [{"role": m.role, "content": m.content} for m in messages]
