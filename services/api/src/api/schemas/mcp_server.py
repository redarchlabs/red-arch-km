"""MCP server schemas. ``secret`` is write-only (encrypted at rest); reads expose
only ``has_secret``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

McpTransport = Literal["stdio", "http", "sse"]


class McpServerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    transport: McpTransport = "http"
    command: str | None = Field(default=None, max_length=4000)
    url: str | None = Field(default=None, max_length=1000)
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=4096)
    enabled: bool = True


class McpServerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    transport: McpTransport | None = None
    command: str | None = Field(default=None, max_length=4000)
    url: str | None = Field(default=None, max_length=1000)
    config: dict[str, Any] | None = None
    secret: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None


class McpServerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    transport: str
    command: str | None
    url: str | None
    config: dict[str, Any]
    enabled: bool
    has_secret: bool = False
    created_at: datetime


class McpToolInfo(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
