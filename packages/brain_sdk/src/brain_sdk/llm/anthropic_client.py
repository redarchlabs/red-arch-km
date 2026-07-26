"""Anthropic (Claude) implementation of the LLM client.

The ``anthropic`` SDK is imported lazily so brain-sdk installs and runs without
it unless a Claude client is actually constructed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from brain_sdk.llm.protocol import LLMMessage

if TYPE_CHECKING:  # the SDK is an optional dependency; types only, never at runtime
    from typing import Literal

    from anthropic.types import MessageParam


class AnthropicLLMClient:
    """LLMClient backed by the Anthropic Messages API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5", *, base_url: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            msg = "anthropic SDK not installed; add 'anthropic' to use a Claude LLM client"
            raise RuntimeError(msg) from exc
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_object: bool = False,
    ) -> str:
        import anthropic  # noqa: PLC0415 - kept lazy (sys.modules cached) so the SDK stays optional

        # Anthropic takes the system prompt as a top-level arg, not a message.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        # The `in (...)` filter guarantees the role is user/assistant but does not
        # narrow `str` to the Literal the SDK's TypedDict requires.
        turns: list[MessageParam] = [
            {"role": cast('Literal["user", "assistant"]', m.role), "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        if json_object:
            system = f"{system}\n\nRespond with a single valid JSON object and nothing else.".strip()
        response = self._client.messages.create(
            model=self._model,
            # The SDK's sentinel for "not supplied" — passing None is a type error
            # and would be sent as an explicit null.
            system=system or anthropic.omit,
            messages=turns,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Match on `.type` rather than getattr: content is a discriminated union, so
        # this narrows to TextBlock and makes `.text` valid. Non-text blocks
        # (thinking, tool use) legitimately have no `.text`.
        return "".join(block.text for block in response.content if block.type == "text")
