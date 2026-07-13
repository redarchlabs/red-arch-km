"""Unit tests for the Claude Code CLI tool: registration, guardrails, authority.

The tool shells the local ``claude`` binary so a single granted agent can offload
dev/ops work to the owner's Max plan. Its safety rests on four things this file pins:

* it is registered ONLY when ``enable_claude_cli_tool`` is set;
* it is ``EXECUTE`` + ``side_effecting`` → ASK under high-touch, and kind-gated to
  operators, so only the granted dev/ops assistant can ever use it;
* it stays inside an allow-listed working dir (traversal is refused);
* it strips ``ANTHROPIC_API_KEY`` from the child env so the CLI uses the subscription,
  not a central API key.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from api.models.agent import Agent
from api.services.agents.authority import Decision, decide
from api.services.agents.tools.claude_code import (
    RUN_CLAUDE_CODE,
    _child_env,
    _resolve_working_dir,
    _run_claude_code,
)
from api.services.agents.tools.registry import base_tool_specs
from api.services.agents.tools.spec import Category, ToolContext

pytestmark = pytest.mark.unit

_EXEC = "asyncio.create_subprocess_exec"


def _agent(kind: str, **grants) -> Agent:
    return Agent(name="a", provider="openai", model="gpt-5-mini", kind=kind, grants=grants)


def _settings(tmp_path, *, allowed=("Read", "Grep"), timeout=300, path="/usr/bin/claude"):
    return SimpleNamespace(
        claude_cli_path=path,
        claude_cli_working_dir=str(tmp_path),
        claude_cli_allowed_tools_list=list(allowed),
        claude_cli_timeout_seconds=timeout,
    )


def _ctx(settings) -> ToolContext:
    return ToolContext(
        session=None, org_id=uuid.uuid4(), settings=settings,
        agent=_agent("operator", tools=["run_claude_code"]),
    )


class _FakeProc:
    """Stand-in for an asyncio subprocess; ``hang`` forces the timeout path."""

    def __init__(self, *, stdout=b"", stderr=b"", returncode=0, hang=False) -> None:
        self._stdout, self._stderr = stdout, stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)  # cancelled by wait_for → TimeoutError
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


# --- guardrail helpers -----------------------------------------------------


def test_resolve_working_dir(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    assert _resolve_working_dir(str(tmp_path), None) == tmp_path.resolve()
    assert _resolve_working_dir(str(tmp_path), "sub") == sub.resolve()
    # Traversal + absolute-path escapes are refused.
    assert _resolve_working_dir(str(tmp_path), "../..") is None
    assert _resolve_working_dir(str(tmp_path), "/etc") is None


def test_child_env_strips_subscription_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-removed")
    monkeypatch.setenv("KM2_ENV_SENTINEL", "keep")
    env = _child_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env.get("KM2_ENV_SENTINEL") == "keep"


# --- handler ---------------------------------------------------------------


async def test_success_parses_json_and_forces_subscription(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-central-key")
    fake = _FakeProc(stdout=json.dumps({"result": "edited 2 files", "is_error": False}).encode())
    with patch(_EXEC, AsyncMock(return_value=fake)) as m:
        out = await _run_claude_code(_ctx(_settings(tmp_path)), {"task": "refactor auth"})

    assert out["result"] == "edited 2 files"
    assert out["is_error"] is False
    assert out["truncated"] is False and "duration_ms" in out
    argv, kwargs = m.call_args
    assert argv[0] == "/usr/bin/claude" and "-p" in argv and "refactor auth" in argv
    assert "--allowedTools" in argv and "Read,Grep" in argv
    assert kwargs["cwd"] == str(tmp_path)
    # The child must NOT carry the central API key, or the CLI bills the API.
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]


async def test_nonzero_exit_returns_error_with_stderr(tmp_path):
    fake = _FakeProc(stderr=b"kaboom", returncode=2)
    with patch(_EXEC, AsyncMock(return_value=fake)):
        out = await _run_claude_code(_ctx(_settings(tmp_path)), {"task": "x"})
    assert "exited 2" in out["error"]
    assert "kaboom" in out["stderr"]


async def test_timeout_kills_process(tmp_path):
    fake = _FakeProc(hang=True)
    with patch(_EXEC, AsyncMock(return_value=fake)):
        out = await _run_claude_code(_ctx(_settings(tmp_path, timeout=0.01)), {"task": "x"})
    assert "timed out" in out["error"]
    assert fake.killed is True


async def test_requires_task(tmp_path):
    out = await _run_claude_code(_ctx(_settings(tmp_path)), {"task": "   "})
    assert out["error"] == "task is required"


async def test_requires_configured_binary_and_root(tmp_path):
    out = await _run_claude_code(_ctx(_settings(tmp_path, path="")), {"task": "x"})
    assert "CLAUDE_CLI_PATH" in out["error"]
    s = _settings(tmp_path)
    s.claude_cli_working_dir = ""
    out2 = await _run_claude_code(_ctx(s), {"task": "x"})
    assert "CLAUDE_CLI_WORKING_DIR" in out2["error"]


async def test_working_dir_escape_refused_without_launching(tmp_path):
    # No subprocess mock: the guard must return before any launch attempt.
    with patch(_EXEC, AsyncMock(side_effect=AssertionError("must not launch"))):
        out = await _run_claude_code(_ctx(_settings(tmp_path)), {"task": "x", "working_dir": "../../etc"})
    assert "escapes" in out["error"]


# --- registration + authority ----------------------------------------------


def test_registered_only_when_enabled():
    assert "run_claude_code" not in {s.name for s in base_tool_specs()}
    assert "run_claude_code" not in {
        s.name for s in base_tool_specs(SimpleNamespace(enable_claude_cli_tool=False))
    }
    assert "run_claude_code" in {
        s.name for s in base_tool_specs(SimpleNamespace(enable_claude_cli_tool=True))
    }


def test_tool_is_execute_and_side_effecting():
    assert RUN_CLAUDE_CODE.category == Category.EXECUTE
    assert RUN_CLAUDE_CODE.side_effecting is True


def test_authority_only_granted_operator_may_run():
    granted = _agent("operator", tools=["run_claude_code"])
    # High-touch: side-effecting → ASK (parks in worker, auto-approved in console).
    assert decide(granted, RUN_CLAUDE_CODE, autonomy="high_touch").decision is Decision.ASK
    # Hands-off isolates the grant mechanic: allowed.
    assert decide(granted, RUN_CLAUDE_CODE, autonomy="hands_off").decision is Decision.ALLOW
    # Operator without the grant: denied.
    assert decide(_agent("operator"), RUN_CLAUDE_CODE).decision is Decision.DENY
    # Non-operators are kind-gated out of EXECUTE even if granted.
    for kind in ("coordinator", "advisory"):
        agent = _agent(kind, tools=["run_claude_code"])
        assert decide(agent, RUN_CLAUDE_CODE).decision is Decision.DENY
