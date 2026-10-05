"""Runs started through an API key carry that origin to everything they spawn.

``via_api_key`` + ``api_key_id`` are what stop a key-originated run from reaching
other orgs and what tie a scoped key's run to the key's masks and folders, reloaded
on every tool call (``tools/key_scope.py``, ``tools/knowledge.py``). A delegated
child or a work-order continuation that dropped them would quietly lose the limits.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.services.agents import delegation

pytestmark = pytest.mark.unit


async def test_delegated_child_inherits_the_flag() -> None:
    key_id = uuid.uuid4()
    caller = SimpleNamespace(id=uuid.uuid4(), name="lead")
    target = SimpleNamespace(id=uuid.uuid4(), supervisor_id=caller.id, provider="openai", model="m")
    repo = MagicMock()
    repo.create_run = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    with (
        patch.object(delegation, "resolve_agent", AsyncMock(return_value=target)),
        patch.object(delegation, "AgentRunRepository", return_value=repo),
    ):
        await delegation.delegate(
            MagicMock(),
            uuid.uuid4(),
            caller,
            "report",
            "do it",
            run_id=uuid.uuid4(),
            work_order_id=None,
            actor_user_id=uuid.uuid4(),
            via_api_key=True,
            api_key_id=key_id,
        )
    assert repo.create_run.await_args.kwargs["via_api_key"] is True
    assert repo.create_run.await_args.kwargs["api_key_id"] == key_id


async def test_delegation_defaults_to_not_via_api_key() -> None:
    caller = SimpleNamespace(id=uuid.uuid4(), name="lead")
    target = SimpleNamespace(id=uuid.uuid4(), supervisor_id=caller.id, provider="openai", model="m")
    repo = MagicMock()
    repo.create_run = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    with (
        patch.object(delegation, "resolve_agent", AsyncMock(return_value=target)),
        patch.object(delegation, "AgentRunRepository", return_value=repo),
    ):
        await delegation.delegate(MagicMock(), uuid.uuid4(), caller, "report", "do it", run_id=None, work_order_id=None)
    assert repo.create_run.await_args.kwargs["via_api_key"] is False


async def test_scoped_key_run_cannot_run_workflows(monkeypatch: pytest.MonkeyPatch) -> None:
    """No actor needed: the key itself decides, reloaded at call time."""
    from api.services.agents.tools import key_scope
    from api.services.agents.tools import workflows as wf_tools

    async def _scoped(_ctx: object) -> key_scope.RunKeyStatus:
        return key_scope.RunKeyStatus(key_scope.KeyState.SCOPED)

    monkeypatch.setattr(key_scope, "run_key_status", _scoped)
    ctx = SimpleNamespace(
        via_api_key=True,
        actor_user_id=None,
        api_key_id=uuid.uuid4(),
        agent=SimpleNamespace(grants={"workflows": ["x"]}),
        session=MagicMock(),
        org_id=uuid.uuid4(),
    )
    out = await wf_tools._run_workflow(ctx, {"workflow_id": "x"})  # noqa: SLF001
    assert "cannot run workflows" in out["error"]


async def test_run_whose_key_is_gone_cannot_run_workflows() -> None:
    """via_api_key with the key deleted (api_key_id NULL) is the most restrictive case."""
    from api.services.agents.tools import workflows as wf_tools

    ctx = SimpleNamespace(
        via_api_key=True,
        actor_user_id=None,
        api_key_id=None,
        agent=SimpleNamespace(grants={"workflows": ["x"]}),
        session=MagicMock(),
        org_id=uuid.uuid4(),
    )
    out = await wf_tools._run_workflow(ctx, {"workflow_id": "x"})  # noqa: SLF001
    assert "cannot run workflows" in out["error"]


async def test_v1_runs_and_work_orders_have_no_actor() -> None:
    """A key is not a person: what /api/v1 creates carries the key, not a profile."""
    from api.auth.api_key import ApiKeyPrincipal
    from api.routers.v1 import agents as v1_agents

    principal = ApiKeyPrincipal(
        api_key_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        scopes=frozenset({"agents:run"}),
        name="k",
        access_mode="scoped",
        masks=(0,),
    )
    agent = SimpleNamespace(id=uuid.uuid4(), enabled=True, provider="openai", model="m")
    run = MagicMock()
    repo = MagicMock()
    repo.create_run = AsyncMock(return_value=run)
    agents_repo = MagicMock()
    agents_repo.get = AsyncMock(return_value=agent)
    with (
        patch.object(v1_agents, "AgentRepository", return_value=agents_repo),
        patch.object(v1_agents, "AgentRunRepository", return_value=repo),
        patch.object(v1_agents.AgentRunRead, "model_validate", return_value="ok"),
    ):
        await v1_agents.trigger_agent_run(agent.id, v1_agents.AgentRunTrigger(task="t"), principal, MagicMock())
    kwargs = repo.create_run.await_args.kwargs
    assert kwargs["actor_user_id"] is None
    assert kwargs["via_api_key"] is True
    assert kwargs["api_key_id"] == principal.api_key_id
