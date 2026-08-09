"""Approvals + escalation inbox service.

Approving an "ask" adds the tool to the parked run's resume-approved set and
re-queues the run, so the worker sweep continues the exact same turn with the tool
now permitted. Denying finalizes the run as an error. This is the human end of the
authority "ask" tier — a gate the runtime never auto-promotes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentApproval, AgentNotification
from api.repositories.agent_inbox import AgentApprovalRepository, AgentNotificationRepository
from api.repositories.agent_run import AgentRunRepository
from api.services.agents import lifecycle


class ApprovalError(Exception):
    pass


class ApprovalNotFoundError(ApprovalError):
    pass


class ApprovalAlreadySettledError(ApprovalError):
    """Someone already decided this one."""


def _require_pending(approval: AgentApproval) -> None:
    """Refuse a second decision instead of quietly reporting success.

    Returning the row unchanged made the losing tab in a two-reviewer race read as
    "approved" when it had approved nothing — and if the first decision was a
    *denial*, the second reviewer was told their approval took effect on a run that
    had already been failed. The question path already raises here; approvals now
    match it, so the first decision wins and the second is told so.
    """
    if approval.status != "pending":
        raise ApprovalAlreadySettledError(f"This request was already {approval.status}")


def _now() -> datetime:
    return datetime.now(UTC)


class ApprovalService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = AgentApprovalRepository(session, org_id)
        self._runs = AgentRunRepository(session, org_id)

    async def list_pending(self) -> list[AgentApproval]:
        return await self._repo.list_pending()

    async def _get(self, approval_id: uuid.UUID) -> AgentApproval:
        approval = await self._repo.get(approval_id)
        if approval is None:
            raise ApprovalNotFoundError(f"Approval {approval_id} not found")
        return approval

    async def approve(self, approval_id: uuid.UUID, decided_by: uuid.UUID | None) -> AgentApproval:
        approval = await self._get(approval_id)
        _require_pending(approval)

        run = await self._runs.get_run(approval.run_id)
        if run is not None and run.status in ("done", "error", "cancelled", "escalated"):
            # The run ended (cancelled/timed out) while the ask was pending — a late
            # approval must NOT re-queue it and re-execute the parked side effects.
            approval.status = "voided"
            approval.decided_by_profile_id = decided_by
            approval.decided_at = _now()
            await self._session.flush()
            return approval

        approval.status = "approved"
        approval.decided_by_profile_id = decided_by
        approval.decided_at = _now()

        if run is not None and run.status == "waiting":
            run_input = dict(run.input or {})
            resume = dict(run_input.get("resume") or {"messages": [], "pending": [], "approved": []})
            approved = set(resume.get("approved") or [])
            approved.add(approval.tool_name)
            resume["approved"] = sorted(approved)
            run_input["resume"] = resume
            run.input = run_input
            run.status = "queued"  # the worker sweep resumes it
            run.wait_kind = None
            run.last_activity_at = _now()
        await self._session.flush()
        return approval

    async def deny(self, approval_id: uuid.UUID, decided_by: uuid.UUID | None) -> AgentApproval:
        approval = await self._get(approval_id)
        _require_pending(approval)
        approval.status = "denied"
        approval.decided_by_profile_id = decided_by
        approval.decided_at = _now()

        run = await self._runs.get_run(approval.run_id)
        if run is not None and run.status == "waiting":
            await lifecycle.finalize_run(
                self._session, self._org_id, run, status="error", error=f"approval denied: {approval.tool_name}"
            )
        await self._session.flush()
        return approval


class NotificationService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = AgentNotificationRepository(session, org_id)

    async def list(self, *, unresolved_only: bool = False) -> list[AgentNotification]:
        return await self._repo.list_all(unresolved_only=unresolved_only)

    async def unread_count(self) -> int:
        return await self._repo.unread_count()

    async def set_status(self, notification_id: uuid.UUID, status: str) -> AgentNotification:
        notification = await self._repo.get(notification_id)
        if notification is None:
            raise ApprovalNotFoundError(f"Notification {notification_id} not found")
        notification.status = status
        await self._session.flush()
        return notification
