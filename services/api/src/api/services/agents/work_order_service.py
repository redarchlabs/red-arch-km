"""Work-order service — lifecycle + task/diary management with typed errors.

Enforces the status state machine (draft → awaiting_approval → approved →
in_progress → done | cancelled) so a caller can't jump an order to an invalid state.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.work_order import WorkOrder, WorkOrderEntry, WorkOrderTask
from api.repositories.work_order import WorkOrderRepository

# Allowed status transitions (terminal states have none).
_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"awaiting_approval", "approved", "in_progress", "cancelled"},
    "awaiting_approval": {"approved", "draft", "cancelled"},
    "approved": {"in_progress", "cancelled"},
    "in_progress": {"done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}


class WorkOrderError(Exception):
    pass


class WorkOrderNotFoundError(WorkOrderError):
    pass


class WorkOrderValidationError(WorkOrderError):
    pass


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "wo"
    return f"{base}-{uuid.uuid4().hex[:6]}"


class WorkOrderService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = WorkOrderRepository(session, org_id)

    async def list_work_orders(self) -> list[WorkOrder]:
        return await self._repo.list_all()

    async def get_work_order(self, wo_id: uuid.UUID) -> WorkOrder:
        wo = await self._repo.get(wo_id)
        if wo is None:
            raise WorkOrderNotFoundError(f"Work order {wo_id} not found")
        return wo

    async def create_work_order(
        self,
        *,
        title: str,
        body: str | None = None,
        priority: str = "normal",
        assigned_agent_id: uuid.UUID | None = None,
        created_by_profile_id: uuid.UUID | None = None,
    ) -> WorkOrder:
        wo = WorkOrder(
            slug=_slugify(title),
            title=title,
            body=body,
            priority=priority,
            assigned_agent_id=assigned_agent_id,
            created_by_profile_id=created_by_profile_id,
            status="draft",
        )
        return await self._repo.create(wo)

    async def set_status(self, wo_id: uuid.UUID, new_status: str) -> WorkOrder:
        wo = await self.get_work_order(wo_id)
        if new_status == wo.status:
            return wo
        allowed = _TRANSITIONS.get(wo.status, set())
        if new_status not in allowed:
            raise WorkOrderValidationError(
                f"Cannot move work order from '{wo.status}' to '{new_status}'"
            )
        wo.status = new_status
        await self._repo.flush()
        return wo

    async def assign(self, wo_id: uuid.UUID, agent_id: uuid.UUID | None) -> WorkOrder:
        wo = await self.get_work_order(wo_id)
        wo.assigned_agent_id = agent_id
        await self._repo.flush()
        return wo

    async def list_tasks(self, wo_id: uuid.UUID) -> list[WorkOrderTask]:
        await self.get_work_order(wo_id)
        return await self._repo.list_tasks(wo_id)

    async def set_tasks(self, wo_id: uuid.UUID, tasks: list[dict]) -> list[WorkOrderTask]:
        await self.get_work_order(wo_id)
        models = [
            WorkOrderTask(
                key=t.get("key") or f"T{i + 1}",
                title=t["title"],
                status=t.get("status", "pending"),
                sort_order=t.get("sort_order", i),
                assigned_agent_id=t.get("assigned_agent_id"),
            )
            for i, t in enumerate(tasks)
        ]
        return await self._repo.replace_tasks(wo_id, models)

    async def add_entry(
        self,
        wo_id: uuid.UUID,
        *,
        text: str,
        agent_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
        role: str | None = None,
    ) -> WorkOrderEntry:
        await self.get_work_order(wo_id)
        return await self._repo.add_entry(
            WorkOrderEntry(
                work_order_id=wo_id, text=text, agent_id=agent_id, agent_run_id=agent_run_id, role=role
            )
        )

    async def list_entries(self, wo_id: uuid.UUID) -> list[WorkOrderEntry]:
        await self.get_work_order(wo_id)
        return await self._repo.list_entries(wo_id)

    def progress(self, tasks: list[WorkOrderTask]) -> float:
        """Percent complete = done / (total excluding carried)."""
        counted = [t for t in tasks if t.status != "carried"]
        if not counted:
            return 0.0
        done = sum(1 for t in counted if t.status == "done")
        return round(done / len(counted), 3)
