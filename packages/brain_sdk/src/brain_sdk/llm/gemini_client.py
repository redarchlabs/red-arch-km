"""Google Gemini implementation of the LLM client.

The ``google-generativeai`` SDK is imported lazily so brain-sdk installs and
runs without it unless a Gemini client is actually constructed.
"""

from __future__ import annotations

from brain_sdk.llm.protocol import LLMMessage


class GeminiLLMClient:
    """LLMClient backed by the Google Gemini API."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        try:
            import google.generativeai as genai  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            msg = "google-generativeai SDK not installed; add it to use a Gemini LLM client"
            raise RuntimeError(msg) from exc
        genai.configure(api_key=api_key)
        self._genai = genai
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
        system = "\n\n".join(m.content for m in messages if m.role == "system") or None
        # Gemini uses "model" for assistant turns and "user" for user turns.
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [m.content]}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        generation_config: dict[str, object] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if json_object:
            generation_config["response_mime_type"] = "application/json"
        model = self._genai.GenerativeModel(
            model_name=self._model,
            system_instruction=system,
            generation_config=generation_config,
        )
        response = model.generate_content(contents)
        return response.text or ""
