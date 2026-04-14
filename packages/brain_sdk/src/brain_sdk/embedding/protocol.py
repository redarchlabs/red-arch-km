"""Protocol for embedding providers."""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Interface for text embedding generation."""

    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text string."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of texts."""
        ...

    @property
    def dimension(self) -> int:
        """Return the dimensionality of the embedding vectors."""
        ...
