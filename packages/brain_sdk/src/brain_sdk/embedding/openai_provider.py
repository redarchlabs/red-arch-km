"""OpenAI embedding provider implementation."""

from __future__ import annotations

import logging

from openai import OpenAI

logger = logging.getLogger(__name__)

# Known dimensions for OpenAI embedding models
_MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbeddingProvider:
    """Generate embeddings using the OpenAI API."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._dimension = _MODEL_DIMENSIONS.get(model, 1536)

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(input=[text], model=self._model)
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]

    @property
    def dimension(self) -> int:
        return self._dimension
