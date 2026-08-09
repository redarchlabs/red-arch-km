"""The link between a work order and the documents that came in and out of it.

``work_order_artifacts`` has existed since migration 030 with no code path. It is
a join, not a store: the document itself lives in the documents subsystem, with its
own ingest, permissions and search. This only records that a given document
belongs to a given order, and which direction it went.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Document
from api.models.work_order import WorkOrderArtifact

# Which way the document went. `input` is something a person handed the agents;
# `output` is something an agent produced. Worth distinguishing: an agent starting
# work wants the inputs, and a person reviewing wants the outputs.
ARTIFACT_KINDS = ("input", "output")


class WorkOrderArtifactRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def attach(
        self,
        work_order_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        kind: str = "output",
        filename: str | None = None,
        mime: str | None = None,
        size: int | None = None,
    ) -> WorkOrderArtifact:
        row = WorkOrderArtifact(
            work_order_id=work_order_id,
            document_id=document_id,
            kind=kind if kind in ARTIFACT_KINDS else "output",
            filename=filename,
            mime=mime,
            size=size,
            org_id=self._org_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for(self, work_order_id: uuid.UUID) -> list[tuple[WorkOrderArtifact, Document | None]]:
        """Artifacts oldest first, each with its document if it still exists.

        The document may be gone — ``document_id`` is ``ON DELETE SET NULL``, so an
        artifact outlives a deleted document rather than vanishing with it. The
        filename is kept on the row for exactly that case: the record still says
        what was attached.
        """
        rows = (
            await self._session.execute(
                select(WorkOrderArtifact, Document)
                .outerjoin(Document, Document.id == WorkOrderArtifact.document_id)
                .where(
                    WorkOrderArtifact.work_order_id == work_order_id,
                    WorkOrderArtifact.org_id == self._org_id,
                )
                .order_by(WorkOrderArtifact.created_at)
            )
        ).all()
        return [(artifact, document) for artifact, document in rows]

    async def get(self, artifact_id: uuid.UUID) -> WorkOrderArtifact | None:
        return (
            await self._session.execute(
                select(WorkOrderArtifact).where(
                    WorkOrderArtifact.id == artifact_id, WorkOrderArtifact.org_id == self._org_id
                )
            )
        ).scalar_one_or_none()

    async def detach(self, artifact_id: uuid.UUID) -> bool:
        """Unlink an artifact. The document itself is untouched.

        Removing the link is not deleting the work: a document attached to the
        wrong order should be movable without destroying it.
        """
        row = await self.get(artifact_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
