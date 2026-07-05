"""Search schemas."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    limit: int = Field(default=5, ge=1, le=50)
    tags: list[str] = Field(default_factory=list)
    folder_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Scope retrieval to these folders (ORed). Translated to folder:<id> tags.",
    )


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    folder_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Scope the chat to these folders (ORed). Translated to folder:<id> tags.",
    )
    use_knowledge_graph: bool = True


class SearchResult(BaseModel):
    id: str
    score: float
    text: str
    document_id: str
    document_key: str
    document_title: str
    chunk_order: int


class SearchResponse(BaseModel):
    hits: list[SearchResult]
    total: int


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]]
    graph_context: list[dict[str, Any]] = []
