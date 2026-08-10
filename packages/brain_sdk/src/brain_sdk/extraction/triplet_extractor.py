"""Extract subject-predicate-object triplets from text using LLM."""

from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from brain_sdk.embedding.openai_provider import _clean_base_url

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Extract knowledge graph triplets from the following text.
Return a JSON array of objects with keys "subject", "predicate", "object".
Each should be a short, normalized phrase. Return only valid JSON, no markdown fences.
If no triplets can be extracted, return an empty array [].
"""


def _clean_json_response(text: str) -> str:
    """Strip markdown code fences from LLM responses."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


class TripletExtractor:
    """Extract knowledge graph triplets from text via LLM."""

    def __init__(self, api_key: str, model: str = "gpt-5.6-luna", base_url: str | None = None) -> None:
        # Explicit base_url — see ChunkSummarizer for why this is never left to default.
        self._client = OpenAI(api_key=api_key, base_url=_clean_base_url(base_url))
        self._model = model

    def extract(self, text: str) -> list[tuple[str, str, str]]:
        """Extract triplets from a text chunk.

        Returns a list of (subject, predicate, object) tuples.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=1000,
            temperature=0.1,
        )

        raw = response.choices[0].message.content or "[]"
        cleaned = _clean_json_response(raw)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse triplet JSON: %s", cleaned[:200])
            return []

        if not isinstance(data, list):
            return []

        triplets: list[tuple[str, str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            s = str(item.get("subject", "")).strip()
            p = str(item.get("predicate", "")).strip()
            o = str(item.get("object", "")).strip()
            if s and p and o:
                triplets.append((s, p, o))

        return triplets
