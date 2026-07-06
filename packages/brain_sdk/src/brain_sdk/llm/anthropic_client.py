"""Anthropic (Claude) implementation of the LLM client.

The ``anthropic`` SDK is imported lazily so brain-sdk installs and runs without
it unless a Claude client is actually constructed.
"""

from __future__ import annotations

from brain_sdk.llm.protocol import LLMMessage


class AnthropicLLMClient:
    """LLMClient backed by the Anthropic Messages API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5", *, base_url: str | None = None) -> None:
        try:
            import anthropic  # type: ignore[import-not-found]
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
        # Anthropic takes the system prompt as a top-level arg, not a message.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        if json_object:
            system = f"{system}\n\nRespond with a single valid JSON object and nothing else.".strip()
        response = self._client.messages.create(
            model=self._model,
            system=system or None,
            messages=turns,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
