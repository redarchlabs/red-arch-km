"""Integration tests for persisting ``documents.celery_task_id`` (real PostgreSQL/RLS).

Every ingest-dispatching route commits the document row *before* enqueueing, so
the task id has to be written back afterwards — in a transaction that no longer
carries the ``SET LOCAL`` tenant scope the first one had. Getting that wrong is
invisible from the response (the id is written to an in-memory attribute and the
route returns 201 either way), so these tests read the column back out of the
database rather than trusting the ORM object.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.document import DocumentRepository
from api.routers.documents import _persist_task_id
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org(session: AsyncSession, prefix: str) -> uuid.UUID:
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}", permission_number=1)
    session.add(org)
    await session.flush()
    await set_tenant(session, str(org.id))
    return org.id


async def _stored_task_id(session: AsyncSession, org_id: uuid.UUID, doc_id: uuid.UUID) -> str | None:
    """Read the column straight from the row, under tenant scope."""
    await set_tenant(session, str(org_id))
    result = await session.execute(
        text("SELECT celery_task_id FROM documents WHERE id = :doc_id"),
        {"doc_id": doc_id},
    )
    return result.scalar_one()


# NOTE: there is deliberately no "bare assignment raises StaleDataError" test.
# That asserts a failure mode of the removed code, and whether the ORM flush
# raises or silently writes nothing depends on when the tenant GUC lapses — it
# passed locally and not in CI. The contract worth pinning is the one below: the
# id is stored, scoped to its own org, and never turned into a request failure.
class TestPersistTaskId:
    async def test_task_id_survives_the_row_commit(self, session: AsyncSession) -> None:
        org_id = await _org(session, "TaskId")
        doc = await DocumentRepository(session, org_id).create(title="Doc", text="body")
        doc_id = doc.id
        # Mirror the routes: the row is committed before the task is dispatched,
        # which ends the transaction and clears the tenant GUC with it.
        await session.commit()

        await _persist_task_id(session, org_id, doc, "task-abc123")

        assert await _stored_task_id(session, org_id, doc_id) == "task-abc123"
        # The response is serialized from the instance, so it must carry the id
        # too — without the instance going dirty again (see _persist_task_id).
        assert doc.celery_task_id == "task-abc123"
        assert doc not in session.dirty

    async def test_persist_is_scoped_to_the_documents_org(self, session: AsyncSession) -> None:
        """The write must not depend on a widened scope: passing another org's id
        leaves the row untouched rather than updating across the tenant boundary.
        """
        org_id = await _org(session, "TaskId")
        doc = await DocumentRepository(session, org_id).create(title="Doc", text="body")
        doc_id = doc.id
        await session.commit()
        other_org_id = await _org(session, "TaskIdOther")
        await session.commit()

        await _persist_task_id(session, other_org_id, doc, "task-crossorg")

        assert await _stored_task_id(session, org_id, doc_id) is None

    async def test_persist_never_raises_when_the_row_is_gone(self, session: AsyncSession) -> None:
        """A dispatched task whose document has since been deleted must not turn a
        successful request into a failure — the helper logs and moves on.
        """
        org_id = await _org(session, "TaskId")
        doc = await DocumentRepository(session, org_id).create(title="Doc", text="body")
        doc_id = doc.id
        await session.commit()
        await set_tenant(session, str(org_id))
        await session.execute(text("DELETE FROM documents WHERE id = :doc_id"), {"doc_id": doc_id})
        await session.commit()

        await _persist_task_id(session, org_id, doc, "task-orphan")
