"""MCP server schemas. ``secret`` is write-only (encrypted at rest); reads expose
only ``has_secret``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

McpTransport = Literal["stdio", "http", "sse"]
McpAuthType = Literal["none", "bearer", "api_key", "oauth"]
McpOAuthIdentity = Literal["org", "user"]


class McpServerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    transport: McpTransport = "http"
    command: str | None = Field(default=None, max_length=4000)
    url: str | None = Field(default=None, max_length=1000)
    config: dict[str, Any] = Field(default_factory=dict)
    auth_type: McpAuthType = "none"
    secret: str | None = Field(default=None, max_length=4096)  # bearer/api_key static secret
    enabled: bool = True
    # OAuth (auth_type == "oauth")
    oauth_identity: McpOAuthIdentity = "org"
    oauth_client_id: str | None = Field(default=None, max_length=512)
    oauth_client_secret: str | None = Field(default=None, max_length=4096)
    oauth_scopes: str | None = Field(default=None, max_length=1000)


class McpServerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    transport: McpTransport | None = None
    command: str | None = Field(default=None, max_length=4000)
    url: str | None = Field(default=None, max_length=1000)
    config: dict[str, Any] | None = None
    auth_type: McpAuthType | None = None
    secret: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    oauth_identity: McpOAuthIdentity | None = None
    oauth_client_id: str | None = Field(default=None, max_length=512)
    oauth_client_secret: str | None = Field(default=None, max_length=4096)
    oauth_scopes: str | None = Field(default=None, max_length=1000)


class McpOAuthStatus(BaseModel):
    oauth: bool = False
    identity: str | None = None
    connected: bool | None = None
    expires_at: datetime | None = None


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
    auth_type: str = "none"
    has_secret: bool = False
    oauth_identity: str = "org"
    oauth_status: McpOAuthStatus = Field(default_factory=McpOAuthStatus)
    created_at: datetime


class McpToolInfo(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class McpPresetInfo(BaseModel):
    key: str
    label: str
    url: str
    transport: str
    auth_type: str
    scopes: str | None
    supports_dcr: bool
    notes: str


class OAuthStartResponse(BaseModel):
    authorization_url: str
