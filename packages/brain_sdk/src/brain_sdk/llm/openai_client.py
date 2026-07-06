"""OpenAI implementation of the provider-agnostic LLM client (the default)."""

from __future__ import annotations

from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from brain_sdk.llm.protocol import LLMMessage, as_provider_messages


class OpenAILLMClient:
    """LLMClient backed by the OpenAI Chat Completions API."""

    def __init__(self, api_key: str, model: str = "gpt-5-mini", *, base_url: str | None = None) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
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
        payload = cast("list[ChatCompletionMessageParam]", as_provider_messages(messages))
        if json_object:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        else:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return response.choices[0].message.content or ""
