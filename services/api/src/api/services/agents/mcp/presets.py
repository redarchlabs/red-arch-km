"""One-click presets for common remote MCP servers.

These prefill the create form (URL / transport / scopes / whether the provider
supports dynamic client registration). URLs + scopes are starting points the admin
can edit — remote MCP endpoints move, and some providers (GitHub, Atlassian) need a
pre-registered OAuth app (``supports_dcr = False`` → the admin supplies a client id
+ secret).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class McpPreset:
    key: str
    label: str
    url: str
    transport: str  # http | sse
    auth_type: str  # always "oauth" for these
    scopes: str | None
    supports_dcr: bool
    notes: str


MCP_PRESETS: tuple[McpPreset, ...] = (
    McpPreset(
        "linear", "Linear", "https://mcp.linear.app/sse", "sse", "oauth",
        None, True, "Sign in with your Linear account; dynamic registration.",
    ),
    McpPreset(
        "atlassian", "Jira & Confluence (Atlassian)", "https://mcp.atlassian.com/v1/sse", "sse", "oauth",
        None, True, "Authorize your Atlassian site (Jira/Confluence).",
    ),
    McpPreset(
        "github", "GitHub", "https://api.githubcopilot.com/mcp/", "http", "oauth",
        "repo read:org read:user", False,
        "Requires a pre-registered GitHub OAuth app — supply its client id + secret.",
    ),
    McpPreset(
        "notion", "Notion", "https://mcp.notion.com/mcp", "http", "oauth",
        None, True, "Authorize the Notion workspaces you want the agent to reach.",
    ),
    McpPreset(
        "sentry", "Sentry", "https://mcp.sentry.dev/mcp", "http", "oauth",
        None, True, "Authorize your Sentry organization.",
    ),
)
