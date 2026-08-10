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
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


# Directory names listed back after a bad ``working_dir``. Enough to recognise the
# right one, short enough not to eat the context window on a crowded root.
_DIR_HINT_CAP = 25


def _available_dirs(root: str) -> str:
    """The subdirectories a caller may actually pick, for the not-found message."""
    try:
        names = sorted(p.name for p in Path(root).expanduser().resolve().iterdir() if p.is_dir())
    except OSError:
        return ""
    if not names:
        return "The working root has no subdirectories — omit working_dir to run at the root."
    shown = ", ".join(names[:_DIR_HINT_CAP])
    extra = f", and {len(names) - _DIR_HINT_CAP} more" if len(names) > _DIR_HINT_CAP else ""
    return (
        f"Directories under the working root: {shown}{extra}. "
        "Omit working_dir to run at the root. This tool does not create directories."
    )


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
        # Name what is actually there. A bare "does not exist" leaves the model with
        # nothing but another guess — observed live: it invented
        # `github/seo-crawler-playwright`, was told only that it was missing, and gave
        # up on the tool entirely rather than trying a directory that exists.
        return {"error": f"working_dir does not exist: {cwd}. {_available_dirs(root)}"}

    allowed = settings.claude_cli_allowed_tools_list
    return await _invoke(binary, task, cwd, allowed, settings.claude_cli_timeout_seconds)


async def _invoke(binary: str, task: str, cwd: Path, allowed: list[str], timeout: int) -> dict[str, Any]:
    """One bounded CLI run. The allow-list is a parameter, not a global read, because
    the read-only web fetch must not inherit whatever the deployment permits here."""
    cmd = [binary, "-p", task, "--output-format", "json"]
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
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        # Say what to do instead. A bare timeout reads as "this tool does not work"
        # and the model abandons it — which is how a two-minute page fetch became a
        # design document about how one might fetch pages.
        return {
            "error": (
                f"Claude Code CLI timed out after {timeout}s. "
                "That budget is one focused job — read some files, fetch and analyse a page, "
                "make a contained edit — not building a project from scratch. Split the work and "
                "call again with the smallest next step, or ask for the finding rather than the tool "
                "that would produce it."
            )
        }

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
        "Delegate ONE focused coding, file, web or shell/ops job to the local Claude Code "
        "CLI, which runs on the owner's machine (their Max plan) inside an allow-listed "
        "working directory. It can read and edit files, run searches, and FETCH PAGES FROM "
        "THE LIVE WEB — so it is a way to inspect a public URL when no web-research key is "
        "configured. Each call is a single bounded invocation of a few minutes: ask for one "
        "concrete outcome ('fetch https://example.com/robots.txt and report its contents', "
        "'summarise the errors in app.log'), not for a project to be built. If a job is too "
        "big, break it up and call again. This runs code on the host — describe the task "
        "precisely, and use only for the owner's own dev/ops work."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "One concrete, self-contained job for the Claude Code CLI, sized to finish "
                    "in a few minutes. State the outcome you want back, not the tooling to build."
                ),
            },
            "working_dir": {
                "type": "string",
                "description": (
                    "Optional EXISTING subdirectory of the configured root to run in. It is not "
                    "created for you — omit this to run at the root, which is right for anything "
                    "that is not about a specific project's files."
                ),
            },
        },
        "required": ["task"],
    },
    category=Category.EXECUTE,
    handler=_run_claude_code,
    side_effecting=True,
)


# --- read-only web fetch ---------------------------------------------------

# The CLI's only tool this is allowed to reach. NOT the deployment's configured
# allow-list: that one is for the dev/ops tool and may include Read, Bash and
# friends. Reading a public page must not become a way to read the host.
_FETCH_ALLOWED_TOOLS = ["WebFetch"]

# Hosts a page fetch must never reach. An agent that can name a URL can otherwise
# name the machine it is running on — including this API's own internal routes and
# the cloud metadata endpoint, which is the classic way a "fetch this page" feature
# turns into credential exfiltration.
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|\[?::1\]?|10\.|192\.168\.|169\.254\.|"
    r"172\.(1[6-9]|2\d|3[01])\.|.*\.local|.*\.internal)",
    re.I,
)


def _public_http(raw: str) -> str | None:
    """The URL to fetch, or None if it is not a public http(s) address."""
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return None if _BLOCKED_HOSTS.match(parsed.hostname) else raw.strip()


def _fetch_brief(url: str, question: str) -> str:
    return (
        f"Fetch {url} using WebFetch and answer this about it: {question}\n\n"
        "Report only what the page actually contains. If the fetch fails, say so and give "
        "the status code — do not describe what the page would probably say. Do not attempt "
        "any other tool; you have only WebFetch."
    )


async def _fetch_web_page(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    settings = ctx.settings
    url = _public_http(str(args.get("url") or ""))
    question = str(args.get("question") or "").strip() or "Summarise the page, including its title and main headings."
    if not url:
        return {"error": "A public http(s) URL is required; private and loopback addresses are refused."}

    binary = (settings.claude_cli_path or "").strip()
    root = (settings.claude_cli_working_dir or "").strip()
    if not binary or not root:
        return {"error": "The local Claude CLI is not configured, so pages cannot be fetched this way."}
    cwd = Path(root).expanduser().resolve()
    if not cwd.is_dir():
        return {"error": f"The configured working root does not exist: {cwd}"}

    out = await _invoke(binary, _fetch_brief(url, question), cwd, _FETCH_ALLOWED_TOOLS, _FETCH_TIMEOUT_SECONDS)
    if "error" in out:
        return out
    return {"url": url, "content": out.get("result"), "truncated": out.get("truncated", False)}


# A single page fetch is fast. Kept well under the dev/ops budget so a hung fetch
# cannot hold a research run open for five minutes.
_FETCH_TIMEOUT_SECONDS = 120

FETCH_WEB_PAGE = ToolSpec(
    name="fetch_web_page",
    description=(
        "Open a public web page and report what is on it. Use this to inspect a specific URL — "
        "a homepage, robots.txt, a sitemap, a competitor's pricing page. Reads the live web on "
        "the owner's Claude subscription, so it needs no API key. Read-only: it cannot change "
        "anything, run commands, or read local files. Give the exact URL plus what you want to "
        "know about it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full public http(s) URL to open."},
            "question": {
                "type": "string",
                "description": "What to look for on the page. Defaults to a summary of its content.",
            },
        },
        "required": ["url"],
    },
    # READ, like web_research and for the same reason: reading a public page is not
    # acting on the world. This is the whole point of the tool existing separately
    # from run_claude_code — that one is EXECUTE because it edits files and runs
    # shell commands, and the kind-gate rightly bars an advisory agent from it. But
    # that left a *research* agent unable to open a web page, which made the one
    # role whose entire job is research the one role structurally incapable of it.
    category=Category.READ,
    handler=_fetch_web_page,
    side_effecting=False,
)
