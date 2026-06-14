"""Chat session schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatSessionCreate(BaseModel):
    chat_data: dict[str, Any] | None = None


class ChatSessionUpdate(BaseModel):
    chat_data: dict[str, Any]


class ChatSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chat_data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ContextFilters(BaseModel):
    """Optional filters to scope RAG retrieval."""

    folder_ids: list[uuid.UUID] = Field(default_factory=list)
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    document_keys: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    """Request body for the chat ask endpoint."""

    query: str = Field(min_length=1, max_length=5000)
    context_filters: ContextFilters | None = None


class ChatMessage(BaseModel):
    """A single message in the chat history."""

    id: uuid.UUID
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime
    sources: list[dict[str, Any]] = Field(default_factory=list)


class ChatData(BaseModel):
    """Structure of chat_data JSONB field."""

    messages: list[ChatMessage] = Field(default_factory=list)
