"""A run started through an API key stops once that key is gone.

The dispatcher raises ``RunFinished("error")`` when the key is deleted, revoked,
expired or invalid (see ``test_bound_key_agent_tools``); the executor records the
reason and finalizes the run as ``error``. A run whose key is already gone when it
is picked up (or resumed) is finalized the same way before any turn is spent.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.config import Settings
from api.services.agents import run_executor
from api.services.agents.run_executor import AgentRunExecutor
from api.services.agents.runtime import RunFinished
from api.services.agents.tools.key_scope import KEY_GONE, KeyState, RunKeyStatus

pytestmark = pytest.mark.unit


@pytest.fixture
def executor() -> AgentRunExecutor:
    return AgentRunExecutor(Settings(secret_key="test"))  # type: ignore[call-arg]


def _run(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "input": {"task": "t", "resume": {"messages": []}},
        "output": None,
        "status": "running",
        "agent_id": uuid.uuid4(),
        "actor_user_id": None,
        "via_api_key": True,
        "api_key_id": uuid.uuid4(),
        "work_order_id": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestFinishTerminal:
    async def test_error_is_recorded_and_finalized_as_error(self, executor: AgentRunExecutor) -> None:
        run, repo, org_id = _run(), MagicMock(add_step=AsyncMock()), uuid.uuid4()
        finished = RunFinished("error", {"reason": KEY_GONE})
        finished.total_tokens = 7
        with patch.object(run_executor.lifecycle, "finalize_run", AsyncMock(return_value=True)) as finalize:
            await executor._finish_terminal(MagicMock(), org_id, run, repo, finished)  # noqa: SLF001

        repo.add_step.assert_awaited_once_with(run.id, kind="error", content={"reason": KEY_GONE})
        kwargs = finalize.await_args.kwargs
        assert kwargs["status"] == "error" and kwargs["error"] == KEY_GONE and kwargs["total_tokens"] == 7
        assert "resume" not in run.input

    async def test_done_and_escalated_are_unchanged(self, executor: AgentRunExecutor) -> None:
        repo = MagicMock(add_step=AsyncMock())
        with patch.object(run_executor.lifecycle, "finalize_run", AsyncMock(return_value=True)) as finalize:
            await executor._finish_terminal(  # noqa: SLF001
                MagicMock(), uuid.uuid4(), _run(), repo, RunFinished("done", {"output": {"a": 1}})
            )
            assert finalize.await_args.kwargs["status"] == "done"
            await executor._finish_terminal(  # noqa: SLF001
                MagicMock(), uuid.uuid4(), _run(), repo, RunFinished("escalated", {"reason": "stuck"})
            )
            assert finalize.await_args.kwargs["status"] == "escalated"
            assert finalize.await_args.kwargs["error"] == "stuck"


class TestStartWithAGoneKey:
    async def test_the_run_is_finalized_before_any_turn(self, executor: AgentRunExecutor) -> None:
        run = _run(input={"task": "t"})
        repo = MagicMock(get_run=AsyncMock(return_value=run))
        agent = SimpleNamespace(enabled=True, provider="openai", model="gpt", name="a")
        with (
            patch.object(run_executor, "AgentRunRepository", return_value=repo),
            patch.object(run_executor, "AgentRepository", return_value=MagicMock(get=AsyncMock(return_value=agent))),
            patch.object(run_executor, "resolve_provider_key", AsyncMock(return_value="k")),
            patch.object(run_executor, "run_key_status", AsyncMock(return_value=RunKeyStatus(KeyState.GONE))),
            patch.object(run_executor, "available_tools") as tools,
            patch.object(run_executor.lifecycle, "finalize_run", AsyncMock(return_value=True)) as finalize,
        ):
            await executor._execute_one(MagicMock(), uuid.uuid4(), run.id)  # noqa: SLF001

        assert finalize.await_args.kwargs == {"status": "error", "error": KEY_GONE}
        tools.assert_not_called()
