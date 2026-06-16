"""HTTP client for the brain-api service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from api.config import Settings

logger = logging.getLogger(__name__)


class BrainAPIClient:
    """Thin async wrapper around the brain-api service."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.brain_api_url.rstrip("/")
        self._api_key = settings.brain_api_key

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key, "Content-Type": "application/json"}

    async def vector_search(
        self,
        *,
        tenant_id: str,
        query: str,
        limit: int = 5,
        access_keys: list[int] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._base_url}/api/vector-search",
                json={
                    "tenant_id": tenant_id,
                    "query": query,
                    "limit": limit,
                    "access_keys": access_keys or [],
                    "tags": tags or [],
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def vector_chat(
        self,
        *,
        tenant_id: str,
        query: str,
        chat_history: list[dict[str, str]] | None = None,
        access_keys: list[int] | None = None,
        tags: list[str] | None = None,
        use_knowledge_graph: bool = True,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self._base_url}/api/vector-chat",
                json={
                    "tenant_id": tenant_id,
                    "query": query,
                    "chat_history": chat_history or [],
                    "access_keys": access_keys or [],
                    "tags": tags or [],
                    "use_knowledge_graph": use_knowledge_graph,
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_document_chunks(self, tenant_id: str, document_key: str, *, limit: int = 500) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self._base_url}/api/documents/{tenant_id}/{document_key}/chunks",
                params={"limit": limit},
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def vector_chat_stream(
        self,
        *,
        tenant_id: str,
        query: str,
        chat_history: list[dict[str, str]] | None = None,
        access_keys: list[int] | None = None,
        tags: list[str] | None = None,
        use_knowledge_graph: bool = True,
    ) -> AsyncIterator[bytes]:
        """Stream raw SSE bytes from the brain-api's /api/v1/ask/stream endpoint.

        The caller is responsible for returning a StreamingResponse that
        forwards these chunks — we don't parse or repackage them so that
        the client sees exactly what brain-api emitted (including timing).
        """
        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream(
                "POST",
                f"{self._base_url}/api/v1/ask/stream",
                json={
                    "tenant_id": tenant_id,
                    "query": query,
                    "chat_history": chat_history or [],
                    "access_keys": access_keys or [],
                    "tags": tags or [],
                    "use_knowledge_graph": use_knowledge_graph,
                },
                headers=self._headers(),
            ) as response,
        ):
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    async def init_tenant(self, tenant_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self._base_url}/api/init-tenant",
                json={"tenant_id": tenant_id},
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def remove_tenant(self, tenant_id: str) -> dict[str, Any]:
        """Ask brain-api to purge a tenant's Qdrant + Neo4j data."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self._base_url}/api/remove-tenant",
                json={"tenant_id": tenant_id},
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def remove_document(self, tenant_id: str, document_key: str) -> dict[str, Any]:
        """Purge a single document's vectors + graph nodes from brain-api."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._base_url}/api/remove-document",
                json={"tenant_id": tenant_id, "document_key": document_key},
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()
