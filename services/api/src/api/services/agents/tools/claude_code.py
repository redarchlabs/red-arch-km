"""Claude Code CLI tool — delegate heavy dev/ops work to the local Claude Code CLI.

This is the owner's personal-assistant capability, NOT a fleet tool. It shells the
``claude`` binary in headless print mode (``-p``) inside an allow-listed working
directory and returns the CLI's result, so the owner's Max-plan subscription can do
the actual coding/ops work while the KM2 agent just orchestrates.

It is deliberately hard to misuse:

* Registered only when ``settings.enable_claude_cli_tool`` is true (see registry.py).
* Granted to exactly one agent (the dev/ops assistant); ``EXECUTE`` + ``side_effecting``
  so the authority engine asks/parks under high-touch. The interactive console — where
  the human is present — auto-approves and streams the ``tool_call`` frame; the worker
  parks for async approval.
* Bounded to ``settings.claude_cli_working_dir``; a ``working_dir`` argument that
  escapes the root is refused, and the tool errors out if the root is unset.
* Passes a conservative ``--allowedTools`` allow-list (read-only by default); never
  ``--dangerously-skip-permissions``.
* Killed after ``settings.claude_cli_timeout_seconds``.
* Strips ``ANTHROPIC_API_KEY`` from the child env so the CLI authenticates with the
  owner's subscription (a central API key would otherwise bill the API and defeat the
  whole point — the CLI prefers an env key over the subscription login).

Only usable where the CLI is installed + authenticated — the host API process, via the
console. The worker container has no CLI, so the dev/ops assistant is provisioned
console-only (no schedule).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

logger = logging.getLogger(__name__)

# Cap the CLI text handed back to the model so a huge run can't blow the context window.
_MAX_RESULT_CHARS = 12_000

# Env vars that would make the CLI bill the API instead of the subscription; removed
# from the child environment so `claude -p` falls back to the owner's login (~/.claude).
_SUBSCRIPTION_OVERRIDE_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _resolve_working_dir(root: str, requested: Any) -> Path | None:
    """Absolute dir to run in, or None if it escapes the allow-listed ``root``.

    ``requested`` is an optional caller-supplied subdirectory; it must resolve to
    somewhere at/under ``root`` (``is_relative_to`` guards ``../`` traversal and an
    absolute path replacing the root).
    """
    root_path = Path(root).expanduser().resolve()
    if requested in (None, ""):
        return root_path
    candidate = (root_path / str(requested)).resolve()
    return candidate if candidate.is_relative_to(root_path) else None


def _child_env() -> dict[str, str]:
    """A copy of the process env with subscription-overriding keys removed."""
    return {k: v for k, v in os.environ.items() if k not in _SUBSCRIPTION_OVERRIDE_ENV}


async def _run_claude_code(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    settings = ctx.settings
    task = str(args.get("task") or "").strip()
    if not task:
        return {"error": "task is required"}

    binary = (settings.claude_cli_path or "").strip()
    root = (settings.claude_cli_working_dir or "").strip()
    if not binary:
        return {"error": "Claude Code CLI is not configured (set CLAUDE_CLI_PATH)."}
    if not root:
        return {"error": "Claude Code CLI working dir is not configured (set CLAUDE_CLI_WORKING_DIR)."}

    cwd = _resolve_working_dir(root, args.get("working_dir"))
    if cwd is None:
        return {"error": "working_dir escapes the allow-listed root; refused."}
    if not cwd.is_dir():
        return {"error": f"working_dir does not exist: {cwd}"}

    cmd = [binary, "-p", task, "--output-format", "json"]
    allowed = settings.claude_cli_allowed_tools_list
    if allowed:
        cmd += ["--allowedTools", ",".join(allowed)]

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=_child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"error": f"Claude Code CLI not found at '{binary}'."}
    except OSError as exc:  # noqa: BLE001 - surface launch failures as a tool error
        return {"error": f"failed to launch Claude Code CLI: {exc}"}

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=settings.claude_cli_timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": f"Claude Code CLI timed out after {settings.claude_cli_timeout_seconds}s."}

    duration_ms = int((time.monotonic() - started) * 1000)
    out = (stdout or b"").decode("utf-8", "replace")
    err = (stderr or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        return {
            "error": f"Claude Code CLI exited {proc.returncode}",
            "stderr": err[-_MAX_RESULT_CHARS:],
            "duration_ms": duration_ms,
        }

    # `claude -p --output-format json` prints a JSON envelope; fall back to raw text.
    result_text = out
    is_error = False
    try:
        payload: Any = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        result_text = str(payload.get("result", out))
        is_error = bool(payload.get("is_error", False))

    return {
        "result": result_text[:_MAX_RESULT_CHARS],
        "truncated": len(result_text) > _MAX_RESULT_CHARS,
        "is_error": is_error,
        "duration_ms": duration_ms,
    }


RUN_CLAUDE_CODE = ToolSpec(
    name="run_claude_code",
    description=(
        "Delegate a coding, file, or shell/ops task to the local Claude Code CLI, which "
        "runs on the owner's machine (their Max plan) inside an allow-listed working "
        "directory. Give a clear, self-contained task; the CLI does the work and returns "
        "a summary. This runs code on the host — describe the task precisely. Use only for "
        "the owner's own dev/ops work."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "A clear, self-contained instruction for the Claude Code CLI.",
            },
            "working_dir": {
                "type": "string",
                "description": "Optional subdirectory (relative to the configured root) to run in.",
            },
        },
        "required": ["task"],
    },
    category=Category.EXECUTE,
    handler=_run_claude_code,
    side_effecting=True,
)
