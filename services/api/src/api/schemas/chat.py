"""Chat session schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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
