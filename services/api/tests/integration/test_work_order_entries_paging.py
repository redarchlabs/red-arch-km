"""Paging the diary from the bottom up.

The diary is read like a conversation: the newest entry is the one you want, and
history is something you scroll back into. Loading all of it eagerly meant an
order with a long agent transcript rendered hundreds of Markdown blocks on every
page load, most of them never looked at.

So the page asks for the newest slice first and walks backwards with a cursor.
The cursor is ``(created_at, id)`` rather than an offset: entries are appended
while you read, and an offset would silently repeat or skip a row every time one
lands between two requests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.models.org import Org
from api.models.work_order import WorkOrderEntry
from api.services.agents.work_order_service import WorkOrderNotFoundError, WorkOrderService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

T0 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


async def _seed(admin_session: AsyncSession, count: int) -> tuple[Org, uuid.UUID]:
    org = Org(name=f"Diary-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(title="Chatty")
    await admin_session.flush()
    admin_session.add_all(
        [
            WorkOrderEntry(
                work_order_id=wo.id,
                org_id=org.id,
                role="agent",
                text=f"entry {i}",
                created_at=T0 + timedelta(minutes=i),
            )
            for i in range(count)
        ]
    )
    await admin_session.commit()
    return org, wo.id


class TestDiaryPaging:
    async def test_newest_page_comes_back_in_reading_order(self, admin_session: AsyncSession) -> None:
        """Selected newest-first, returned oldest-first: the caller renders the
        slice top-to-bottom without reversing it, and the newest sits at the
        bottom where the eye already is."""
        org, wo_id = await _seed(admin_session, 10)
        svc = WorkOrderService(admin_session, org.id)

        page = await svc.list_entries_page(wo_id, limit=3)

        assert [e.text for e in page.entries] == ["entry 7", "entry 8", "entry 9"]
        assert page.has_more is True

    async def test_the_cursor_walks_backwards_without_gaps(self, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session, 10)
        svc = WorkOrderService(admin_session, org.id)

        first = await svc.list_entries_page(wo_id, limit=4)
        second = await svc.list_entries_page(wo_id, limit=4, before=first.entries[0].id)

        assert [e.text for e in second.entries] == ["entry 2", "entry 3", "entry 4", "entry 5"]

    async def test_the_last_page_says_there_is_no_more(self, admin_session: AsyncSession) -> None:
        """Without this the client keeps firing a request at the top of every
        scroll, forever."""
        org, wo_id = await _seed(admin_session, 3)
        svc = WorkOrderService(admin_session, org.id)

        page = await svc.list_entries_page(wo_id, limit=10)

        assert [e.text for e in page.entries] == ["entry 0", "entry 1", "entry 2"]
        assert page.has_more is False

    async def test_a_new_entry_mid_scroll_does_not_shift_the_page(self, admin_session: AsyncSession) -> None:
        """The reason the cursor is a row and not an offset: an agent writing to
        the diary while you read backwards would otherwise repeat or skip a row."""
        org, wo_id = await _seed(admin_session, 6)
        svc = WorkOrderService(admin_session, org.id)
        first = await svc.list_entries_page(wo_id, limit=2)
        admin_session.add(
            WorkOrderEntry(
                work_order_id=wo_id,
                org_id=org.id,
                role="agent",
                text="arrived late",
                created_at=T0 + timedelta(minutes=99),
            )
        )
        await admin_session.commit()

        second = await svc.list_entries_page(wo_id, limit=2, before=first.entries[0].id)

        assert [e.text for e in second.entries] == ["entry 2", "entry 3"]

    async def test_an_unknown_cursor_is_rejected_rather_than_ignored(self, admin_session: AsyncSession) -> None:
        """Silently returning the newest page would look to the reader like the
        history jumped back to the present."""
        org, wo_id = await _seed(admin_session, 3)
        svc = WorkOrderService(admin_session, org.id)

        with pytest.raises(WorkOrderNotFoundError):
            await svc.list_entries_page(wo_id, limit=2, before=uuid.uuid4())
