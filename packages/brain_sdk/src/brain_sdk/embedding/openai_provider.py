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

OPENAI_API_BASE = "https://api.openai.com/v1"


def _clean_base_url(value: object) -> str:
    """Normalise a configured base URL, falling back to OpenAI.

    Only a real ``str`` counts. Settings objects are frequently ``MagicMock``\\ ed in
    tests, and every mock attribute is truthy — without the type check a mock sails
    through as a URL and fails deep inside httpx with an unhelpful error instead of
    here.
    """
    if not isinstance(value, str):
        return OPENAI_API_BASE
    return value.strip() or OPENAI_API_BASE


class OpenAIEmbeddingProvider:
    """Generate embeddings using any OpenAI-compatible embeddings endpoint.

    ``base_url`` points this at a self-hosted server (llama.cpp started with
    ``--embeddings``, vLLM, Ollama…) instead of OpenAI. It is passed to the client
    **explicitly, always** — never left to default. The OpenAI SDK falls back to the
    ``OPENAI_BASE_URL`` environment variable when the argument is omitted, so a
    process-wide variable intended to redirect *chat* silently redirects embeddings
    too. That is not hypothetical: it sent embeddings to a chat-only llama-server,
    which answers ``501 This server does not support embeddings``, and broke every
    document ingest until it was found.

    ``dimension`` must be supplied for non-OpenAI models, whose vector width this
    module cannot know. Getting it wrong is not a soft failure — the vector store
    collection and the Neo4j vector index are both created at this width, so a
    mismatch corrupts retrieval rather than raising.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
        dimension: int | None = None,
    ) -> None:
        resolved = _clean_base_url(base_url)
        # Self-hosted endpoints authenticate nothing, but the SDK requires *some*
        # key, so supply a placeholder rather than forcing callers to invent one.
        self._client = OpenAI(
            api_key=api_key or ("not-needed" if resolved != OPENAI_API_BASE else ""),
            base_url=resolved,
        )
        self._model = model
        self._base_url = resolved
        # `isinstance` for the same reason as _clean_base_url: a MagicMock dimension
        # would raise comparing against 0 rather than falling through to the model map.
        if isinstance(dimension, int) and not isinstance(dimension, bool) and dimension > 0:
            self._dimension = dimension
        elif model in _MODEL_DIMENSIONS:
            self._dimension = _MODEL_DIMENSIONS[model]
        elif resolved != OPENAI_API_BASE:
            raise ValueError(
                f"embedding dimension is required for self-hosted model {model!r} "
                f"at {resolved} — set EMBEDDING_DIMENSION to the model's vector width"
            )
        else:
            self._dimension = 1536

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

    @property
    def base_url(self) -> str:
        return self._base_url
