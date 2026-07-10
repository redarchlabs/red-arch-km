"""Unit tests for the inline entity-change dispatch path
(`entity_records._dispatch_inline_workflows`) and `OutboxWriter.write` returning
its row — the wiring that lets an inline run key off THIS request's exact outbox
event instead of re-querying (which would race a concurrent writer)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.routers import entity_records


class _AsyncCM:
    """Minimal async context manager standing in for session.begin_nested()."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *_exc):
        return False


def _session() -> MagicMock:
    s = MagicMock()
    s.begin_nested = MagicMock(return_value=_AsyncCM())
    return s


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        workflow_webhook_allowlist=[],
        workflow_trusted_local_hosts=[],
        public_base_url="http://test",
        org_encryption_key=SimpleNamespace(get_secret_value=lambda: "k"),
    )


def _outbox_row(*, org_id, entity_definition_id, record_id, operation="update"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        seq=42,
        created_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        entity_definition_id=entity_definition_id,
        entity_table="e_abc",
        record_id=record_id,
        operation=operation,
        before_data={"alert_level": "Green"},
        after_data={"alert_level": "Red"},
        origin_run_id=None,
        source="record",
    )


class TestDispatchInlineWorkflows:
    @pytest.mark.asyncio
    async def test_none_event_is_noop(self) -> None:
        # No outbox row captured (e.g. a no-op update) → never touches the DB/dispatcher.
        with patch.object(entity_records, "WorkflowRepository") as wf_repo:
            await entity_records._dispatch_inline_workflows(_session(), uuid.uuid4(), None, _settings())
        wf_repo.assert_not_called()

    @pytest.mark.asyncio
    async def test_gate_skips_when_no_inline_workflow(self) -> None:
        org_id = uuid.uuid4()
        row = _outbox_row(org_id=org_id, entity_definition_id=uuid.uuid4(), record_id=uuid.uuid4())
        wf_repo = MagicMock()
        wf_repo.has_inline_for_entity = AsyncMock(return_value=False)
        with (
            patch.object(entity_records, "WorkflowRepository", return_value=wf_repo),
            patch.object(entity_records, "WorkflowDispatchService") as disp,
        ):
            await entity_records._dispatch_inline_workflows(_session(), org_id, row, _settings())
        wf_repo.has_inline_for_entity.assert_awaited_once_with(row.entity_definition_id)
        disp.assert_not_called()  # gated out before building a dispatcher

    @pytest.mark.asyncio
    async def test_dispatches_this_rows_event(self) -> None:
        """The event handed to run_inline_for_change is built from THIS row (the HIGH
        fix): id/seq/created_at/before/after all come from the captured outbox row."""
        org_id = uuid.uuid4()
        row = _outbox_row(org_id=org_id, entity_definition_id=uuid.uuid4(), record_id=uuid.uuid4())
        wf_repo = MagicMock()
        wf_repo.has_inline_for_entity = AsyncMock(return_value=True)
        dispatcher = MagicMock()
        dispatcher.run_inline_for_change = AsyncMock(return_value={"runs": 1})
        with (
            patch.object(entity_records, "WorkflowRepository", return_value=wf_repo),
            patch.object(entity_records, "WorkflowDispatchService", return_value=dispatcher),
            patch.object(entity_records, "EmailSender", MagicMock()),
        ):
            await entity_records._dispatch_inline_workflows(_session(), org_id, row, _settings())
        dispatcher.run_inline_for_change.assert_awaited_once()
        event = dispatcher.run_inline_for_change.await_args.args[0]
        assert event["id"] == row.id
        assert event["seq"] == row.seq
        assert event["created_at"] == row.created_at
        assert event["record_id"] == row.record_id
        assert event["before_data"] == {"alert_level": "Green"}
        assert event["after_data"] == {"alert_level": "Red"}
        assert event["origin_run_id"] is None

    @pytest.mark.asyncio
    async def test_dispatch_failure_is_swallowed(self) -> None:
        # A failing inline run must NOT propagate (record write must still commit).
        org_id = uuid.uuid4()
        row = _outbox_row(org_id=org_id, entity_definition_id=uuid.uuid4(), record_id=uuid.uuid4())
        wf_repo = MagicMock()
        wf_repo.has_inline_for_entity = AsyncMock(return_value=True)
        dispatcher = MagicMock()
        dispatcher.run_inline_for_change = AsyncMock(side_effect=RuntimeError("robot down"))
        with (
            patch.object(entity_records, "WorkflowRepository", return_value=wf_repo),
            patch.object(entity_records, "WorkflowDispatchService", return_value=dispatcher),
            patch.object(entity_records, "EmailSender", MagicMock()),
        ):
            # Must not raise.
            await entity_records._dispatch_inline_workflows(_session(), org_id, row, _settings())


class TestOutboxWriterReturnsRow:
    @pytest.mark.asyncio
    async def test_write_returns_the_flushed_row(self) -> None:
        from api.repositories.workflow import OutboxWriter

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        writer = OutboxWriter(session)
        row = await writer.write(
            org_id=uuid.uuid4(),
            entity_definition_id=uuid.uuid4(),
            entity_table="e_abc",
            operation="update",
            record_id=uuid.uuid4(),
            before={"a": 1},
            after={"a": 2},
        )
        # The row that was add()ed is the one returned (so the caller can key an
        # inline dispatch to this exact event).
        session.add.assert_called_once_with(row)
        session.flush.assert_awaited_once()
        assert row.operation == "update"
