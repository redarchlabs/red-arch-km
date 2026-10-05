"""Which tools a run started by a scoped API key may use.

A scoped key reads and writes knowledge with its own masks and folders. The agent
it starts has no actor and must not reach org-wide data through any other tool, so
such a run is limited to an allowlist (``SCOPED_KEY_RUN_TOOLS``): tools that respect
the key's masks, or that touch only the run's own work order, its peers, a person,
or the public web. Everything else — records, workflows, work-order artifacts,
other runs' details, batch generation, local execution and every MCP tool — is
refused, both when the tool list is offered and again when a call is dispatched.

Whether a run is "scoped" comes from reloading its key (``run_key_status`` — one
primary-key read, never the full scope resolution), never from ``actor_user_id``.
A key that is gone (deleted, revoked, expired, invalid) refuses EVERY tool call,
allowlisted ones included, and ends the run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.services.agents import runtime
from api.services.agents.tools import documents as doc_tools
from api.services.agents.tools import key_scope
from api.services.agents.tools.key_scope import (
    KEY_GONE,
    SCOPED_KEY_RUN_TOOLS,
    KeyState,
    RunKeyRefused,
    RunKeyStatus,
    offered_to_run,
    scoped_key_refusal,
)
from api.services.agents.tools.spec import Category, ToolSpec

pytestmark = pytest.mark.unit


@dataclass
class _Ctx:
    via_api_key: bool = False
    actor_user_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    session: Any = None
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent: Any = None
    settings: Any = None
    run_id: uuid.UUID | None = None
    work_order_id: uuid.UUID | None = None
    tool_call_id: str | None = None


_SCOPED = RunKeyStatus(state=KeyState.SCOPED, scopes=frozenset({"agents:run"}))
_ORG = RunKeyStatus(state=KeyState.ORG, scopes=frozenset({"agents:run"}))
_GONE = RunKeyStatus(state=KeyState.GONE)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What ``run_key_status`` returns for key-started runs. The full resolution
    (``run_key_scope``) must never be needed just to gate a tool."""
    state: dict[str, Any] = {"status": _SCOPED}

    async def _status(ctx: Any) -> RunKeyStatus | None:
        return state["status"] if ctx.via_api_key else None

    async def _no_full_resolve(_ctx: Any) -> None:
        raise AssertionError("gating a tool must not run the full key-scope resolution")

    monkeypatch.setattr(key_scope, "run_key_status", _status)
    monkeypatch.setattr(key_scope, "run_key_scope", _no_full_resolve)
    return state


def _key_run() -> _Ctx:
    return _Ctx(via_api_key=True, api_key_id=uuid.uuid4())


def _spec(name: str, handler: Any = None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="d",
        parameters={"type": "object", "properties": {}},
        category=Category.READ,
        handler=handler or AsyncMock(return_value={"ok": True}),
    )


def test_allowlist_is_exactly_the_documented_set() -> None:
    assert (
        frozenset(
            {
                "search_knowledge",
                "create_document",
                "set_work_order_tasks",
                "update_work_order_task",
                "list_work_order_tasks",
                "submit_plan",
                "complete_task",
                "escalate_task",
                "delegate_task",
                "escalate",
                "consult_peer",
                "reply_to_peer",
                "ask_human",
                "request_review",
                "web_research",
                "fetch_web_page",
            }
        )
        == SCOPED_KEY_RUN_TOOLS
    )


_OTHER_TOOLS = [
    "list_records",
    "get_record",
    "create_record",
    "update_record",
    "attach_document",
    "list_work_order_documents",
    "read_work_order_document",
    "list_workflows",
    "run_workflow",
    "read_run_detail",
    "batch_generate",
    "check_batch",
    "run_claude_code",
    "mcp__drive__search",
]


@pytest.mark.parametrize("name", _OTHER_TOOLS)
async def test_other_tools_are_refused_for_a_scoped_key_run(key: dict[str, Any], name: str) -> None:
    assert await scoped_key_refusal(_key_run(), name) is not None


@pytest.mark.parametrize("name", sorted(SCOPED_KEY_RUN_TOOLS))
async def test_allowlisted_tools_pass(key: dict[str, Any], name: str) -> None:
    assert await scoped_key_refusal(_key_run(), name) is None


@pytest.mark.parametrize("name", ["create_record", *sorted(SCOPED_KEY_RUN_TOOLS)])
async def test_a_key_that_is_gone_refuses_every_tool_with_the_key_gone_message(key: dict[str, Any], name: str) -> None:
    key["status"] = _GONE
    assert await scoped_key_refusal(_key_run(), name) == KEY_GONE


@pytest.mark.parametrize("status", [_GONE, _ORG])
async def test_an_org_key_that_is_gone_is_refused_too(key: dict[str, Any], status: RunKeyStatus) -> None:
    key["status"] = status
    expected = KEY_GONE if status is _GONE else None
    assert await scoped_key_refusal(_key_run(), "web_research") == expected


async def test_a_gone_key_run_is_offered_only_the_allowlist(key: dict[str, Any]) -> None:
    key["status"] = _GONE
    specs = [_spec("search_knowledge"), _spec("create_record")]
    assert [s.name for s in await offered_to_run(_key_run(), specs)] == ["search_knowledge"]


async def test_scoped_does_not_depend_on_the_actor(key: dict[str, Any]) -> None:
    ctx = _key_run()
    ctx.actor_user_id = uuid.uuid4()
    assert await scoped_key_refusal(ctx, "create_record") is not None


@pytest.mark.parametrize("ctx", [_Ctx(), _Ctx(actor_user_id=uuid.uuid4())])
async def test_person_runs_are_not_restricted(key: dict[str, Any], ctx: _Ctx) -> None:
    assert await scoped_key_refusal(ctx, "create_record") is None


async def test_org_key_runs_are_not_restricted(key: dict[str, Any]) -> None:
    key["status"] = _ORG
    assert await scoped_key_refusal(_key_run(), "create_record") is None


async def test_offered_tools_are_filtered(key: dict[str, Any]) -> None:
    specs = [_spec("search_knowledge"), _spec("create_record"), _spec("mcp__x__y")]
    assert [s.name for s in await offered_to_run(_key_run(), specs)] == ["search_knowledge"]
    assert [s.name for s in await offered_to_run(_Ctx(), specs)] == ["search_knowledge", "create_record", "mcp__x__y"]


async def test_dispatch_refuses_without_calling_the_handler(key: dict[str, Any]) -> None:
    handler = AsyncMock(return_value={"ok": True})
    tc = runtime.ToolCallRequest(id="c1", name="create_record", arguments={})
    out = await runtime._run_tool(_spec("create_record", handler), _key_run(), tc)  # noqa: SLF001
    assert "error" in out
    handler.assert_not_awaited()


@pytest.mark.parametrize("name", ["web_research", "delegate_task", "ask_human", "create_record"])
async def test_dispatch_with_a_gone_key_ends_the_run(key: dict[str, Any], name: str) -> None:
    """Even an allowlisted tool: the key no longer grants anything, so the run stops
    (status "error", the reason recorded) instead of carrying on without it."""
    key["status"] = _GONE
    handler = AsyncMock(return_value={"ok": True})
    tc = runtime.ToolCallRequest(id="c1", name=name, arguments={})
    with pytest.raises(runtime.RunFinished) as exc:
        await runtime._run_tool(_spec(name, handler), _key_run(), tc)  # noqa: SLF001
    assert exc.value.status == "error"
    assert exc.value.payload["reason"] == KEY_GONE
    handler.assert_not_awaited()


async def test_dispatch_refuses_when_the_key_lookup_itself_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_ctx: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(key_scope, "run_key_status", _boom)
    handler = AsyncMock(return_value={"ok": True})
    tc = runtime.ToolCallRequest(id="c1", name="create_record", arguments={})
    out = await runtime._run_tool(_spec("create_record", handler), _key_run(), tc)  # noqa: SLF001
    assert "error" in out
    handler.assert_not_awaited()


class TestRunKeyStatusLoading:
    """``run_key_status``: one primary-key read, no scope resolution."""

    class _Session:
        def __init__(self, key: Any) -> None:
            self._key = key
            self.gets = 0

        async def get(self, _model: Any, _id: Any, **_kw: Any) -> Any:
            self.gets += 1
            return self._key

        async def execute(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("the status check must not run queries beyond the key lookup")

    def _key(self, ctx: _Ctx, **over: Any) -> Any:
        from types import SimpleNamespace

        base = {"org_id": ctx.org_id, "revoked_at": None, "expires_at": None, "scopes": ["agents:run"]}
        base.update({"access_mode": "scoped", **over})
        return SimpleNamespace(**base)

    async def test_not_a_key_run(self) -> None:
        assert await key_scope.run_key_status(_Ctx()) is None

    async def test_scoped_key_is_one_read(self) -> None:
        ctx = _Ctx(via_api_key=True, api_key_id=uuid.uuid4())
        ctx.session = self._Session(self._key(ctx))
        status = await key_scope.run_key_status(ctx)
        assert status == RunKeyStatus(state=KeyState.SCOPED, scopes=frozenset({"agents:run"}))
        assert ctx.session.gets == 1

    @pytest.mark.parametrize(
        "over",
        [
            {"revoked_at": datetime(2020, 1, 1, tzinfo=UTC)},
            {"expires_at": datetime(2020, 1, 1, tzinfo=UTC)},
            {"access_mode": "bogus"},
            {"org_id": uuid.uuid4()},
        ],
    )
    async def test_revoked_expired_invalid_or_foreign_is_gone(self, over: dict[str, Any]) -> None:
        ctx = _Ctx(via_api_key=True, api_key_id=uuid.uuid4())
        ctx.session = self._Session(self._key(ctx, **over))
        assert (await key_scope.run_key_status(ctx)).state is KeyState.GONE  # type: ignore[union-attr]

    async def test_deleted_or_unset_key_is_gone(self) -> None:
        ctx = _Ctx(via_api_key=True, session=self._Session(None))
        assert (await key_scope.run_key_status(ctx)).state is KeyState.GONE  # type: ignore[union-attr]
        ctx = _Ctx(via_api_key=True, api_key_id=uuid.uuid4(), session=self._Session(None))
        assert (await key_scope.run_key_status(ctx)).state is KeyState.GONE  # type: ignore[union-attr]


class TestRunKeyScopeLoading:
    """``run_key_scope`` itself, against a fake session."""

    class _Session:
        def __init__(self, key: Any) -> None:
            self._key = key

        async def get(self, _model: Any, _id: Any, **_kw: Any) -> Any:
            return self._key

    async def test_not_a_key_run(self) -> None:
        assert await key_scope.run_key_scope(_Ctx()) is None

    async def test_missing_key_id_is_refused(self) -> None:
        with pytest.raises(RunKeyRefused):
            await key_scope.run_key_scope(_Ctx(via_api_key=True, session=self._Session(None)))

    async def test_deleted_key_is_refused(self) -> None:
        with pytest.raises(RunKeyRefused):
            await key_scope.run_key_scope(_Ctx(via_api_key=True, api_key_id=uuid.uuid4(), session=self._Session(None)))

    async def test_key_from_another_org_is_refused(self) -> None:
        from types import SimpleNamespace

        other = SimpleNamespace(org_id=uuid.uuid4(), revoked_at=None, expires_at=None, scopes=[], access_mode="org")
        with pytest.raises(RunKeyRefused):
            await key_scope.run_key_scope(_Ctx(via_api_key=True, api_key_id=uuid.uuid4(), session=self._Session(other)))

    async def test_org_key(self) -> None:
        from types import SimpleNamespace

        ctx = _Ctx(via_api_key=True, api_key_id=uuid.uuid4())
        k = SimpleNamespace(
            org_id=ctx.org_id, revoked_at=None, expires_at=None, scopes=["agents:run"], access_mode="org"
        )
        ctx.session = self._Session(k)
        scope = await key_scope.run_key_scope(ctx)
        assert scope is not None and not scope.scoped and scope.masks is None


class TestCreateDocumentMetadata:
    """For every run, not just key-started ones: metadata must be an object and may
    not set the index's reserved fields."""

    @pytest.mark.parametrize("meta", [["a"], "text", 3, {"access_keys": [0]}, {"tags": ["x"]}, {"tenant_id": "x"}])
    async def test_rejected(self, meta: Any) -> None:
        out = await doc_tools.CREATE_DOCUMENT.handler(_Ctx(), {"title": "t", "text": "x", "metadata": meta})
        assert "error" in out
        assert "metadata" in out["error"]
