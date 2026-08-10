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
from api.services.agents.kind_gate import kind_gate
from api.services.agents.tools.claude_code import (
    FETCH_WEB_PAGE,
    RUN_CLAUDE_CODE,
    _child_env,
    _fetch_web_page,
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
        session=None,
        org_id=uuid.uuid4(),
        settings=settings,
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


async def test_the_timeout_says_how_to_succeed_next_time(tmp_path):
    """A bare timeout reads as "this tool does not work" and the model stops using it.
    Observed live: an agent asked the CLI to build a crawler, blew the budget, then
    told a person it could not reach the web at all — and wrote a design document
    about crawling instead of fetching the page."""
    fake = _FakeProc(hang=True)
    with patch(_EXEC, AsyncMock(return_value=fake)):
        out = await _run_claude_code(_ctx(_settings(tmp_path, timeout=0.01)), {"task": "build a crawler"})

    assert "smallest next step" in out["error"]
    assert "not building a project from scratch" in out["error"]


async def test_a_missing_working_dir_names_the_ones_that_exist(tmp_path):
    """Naming only what is absent leaves the model with another guess. It invented
    `seo-crawler-playwright`, was told only that it did not exist, and gave up on the
    tool rather than trying a directory that does."""
    (tmp_path / "red-arch-km-2").mkdir()
    (tmp_path / "reachy-virtual-robot").mkdir()

    with patch(_EXEC, AsyncMock()) as m:
        out = await _run_claude_code(
            _ctx(_settings(tmp_path)), {"task": "crawl", "working_dir": "seo-crawler-playwright"}
        )

    assert "does not exist" in out["error"]
    assert "red-arch-km-2" in out["error"] and "reachy-virtual-robot" in out["error"]
    assert "does not create directories" in out["error"]
    # Nothing was launched — the guess is refused before the subprocess.
    m.assert_not_called()


async def test_an_empty_root_says_to_run_at_the_root(tmp_path):
    with patch(_EXEC, AsyncMock()):
        out = await _run_claude_code(_ctx(_settings(tmp_path)), {"task": "x", "working_dir": "nope"})

    assert "no subdirectories" in out["error"]


def test_the_description_says_it_can_reach_the_web(tmp_path):
    """The one fact that would have saved the SEO order: with no web-research key
    configured, this tool is still a way to open a public URL."""
    assert "LIVE WEB" in RUN_CLAUDE_CODE.description
    assert "single bounded invocation" in RUN_CLAUDE_CODE.description


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
    assert "run_claude_code" not in {s.name for s in base_tool_specs(SimpleNamespace(enable_claude_cli_tool=False))}
    assert "run_claude_code" in {s.name for s in base_tool_specs(SimpleNamespace(enable_claude_cli_tool=True))}


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


# --- read-only web fetch ---------------------------------------------------


class TestFetchWebPage:
    """The tool that makes an advisory researcher able to research.

    `run_claude_code` is EXECUTE — rightly, it edits files and runs shell commands —
    and the kind-gate bars an advisory agent from EXECUTE before grants are even
    read. That left research-analyst, whose entire job is research, unable to open a
    web page: its only web tool was web_research, and with no key it could do
    nothing at all. Observed live over seven hours and four re-plans.
    """

    def test_it_is_read_only_so_an_adviser_may_hold_it(self) -> None:
        assert FETCH_WEB_PAGE.category == Category.READ
        assert FETCH_WEB_PAGE.side_effecting is False
        assert kind_gate("advisory", FETCH_WEB_PAGE) is None
        assert kind_gate("coordinator", FETCH_WEB_PAGE) is None

    def test_it_is_registered_beside_the_dev_tool(self) -> None:
        enabled = SimpleNamespace(enable_claude_cli_tool=True)
        names = {s.name for s in base_tool_specs(enabled)}
        assert {"fetch_web_page", "run_claude_code"} <= names
        assert "fetch_web_page" not in {s.name for s in base_tool_specs(SimpleNamespace(enable_claude_cli_tool=False))}

    async def test_it_fetches_with_only_webfetch_allowed(self, tmp_path) -> None:
        """Not the deployment's configured allow-list. That one is for the dev/ops
        tool and may include Read and Bash; reading a public page must not become a
        way to read the host."""
        fake = _FakeProc(stdout=json.dumps({"result": "404 Not Found", "is_error": False}).encode())
        with patch(_EXEC, AsyncMock(return_value=fake)) as m:
            out = await _fetch_web_page(
                _ctx(_settings(tmp_path, allowed=("Read", "Bash", "Edit"))),
                {"url": "https://redarchlabs.com/robots.txt", "question": "what does it contain?"},
            )

        assert out["content"] == "404 Not Found"
        argv, _ = m.call_args
        assert "--allowedTools" in argv
        assert argv[argv.index("--allowedTools") + 1] == "WebFetch"
        assert "Bash" not in argv and "Edit" not in argv

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/api/internal/agents/advance-runs",
            "http://127.0.0.1/secrets",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://db.internal/dump",
            "file:///etc/passwd",
            "not a url",
        ],
    )
    async def test_it_refuses_anything_that_is_not_a_public_page(self, tmp_path, url: str) -> None:
        """An agent that can name a URL can otherwise name the machine it runs on —
        including this API's own internal routes and the cloud metadata endpoint."""
        with patch(_EXEC, AsyncMock()) as m:
            out = await _fetch_web_page(_ctx(_settings(tmp_path)), {"url": url})

        assert "public http(s) URL is required" in out["error"]
        m.assert_not_called()

    async def test_a_url_is_required(self, tmp_path) -> None:
        out = await _fetch_web_page(_ctx(_settings(tmp_path)), {})
        assert "error" in out

    async def test_it_says_so_when_the_cli_is_not_configured(self, tmp_path) -> None:
        out = await _fetch_web_page(_ctx(_settings(tmp_path, path="")), {"url": "https://example.com"})
        assert "not configured" in out["error"]

    async def test_the_brief_forbids_inventing_the_page(self, tmp_path) -> None:
        # The failure this prevents is a model describing what a page probably says.
        fake = _FakeProc(stdout=json.dumps({"result": "ok"}).encode())
        with patch(_EXEC, AsyncMock(return_value=fake)) as m:
            await _fetch_web_page(_ctx(_settings(tmp_path)), {"url": "https://example.com"})

        prompt = m.call_args[0][2]
        assert "do not describe what the page would probably say" in prompt.lower()

    async def test_an_advisory_researcher_is_actually_offered_it(self) -> None:
        """The whole point, at the authority layer: grants plus kind must resolve to
        something other than DENY for the agent that needs it."""
        analyst = _agent("advisory", tools=["fetch_web_page"])
        assert decide(analyst, FETCH_WEB_PAGE).decision is not Decision.DENY
        # …and it still cannot reach the dev/ops tool.
        assert decide(_agent("advisory", tools=["run_claude_code"]), RUN_CLAUDE_CODE).decision is Decision.DENY
