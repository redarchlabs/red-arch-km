"""Document repository."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.document import Document, Tag


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(
            select(Document)
            .where(Document.id == document_id)
            .options(selectinload(Document.tags), selectinload(Document.folder))
        )
        return result.scalar_one_or_none()

    async def get_by_key(self, document_key: str) -> Document | None:
        """Resolve a document by its ``document_key`` (the id shared with the
        vector store). Chat/search sources reference documents by key, so this
        maps a key back to the canonical Postgres row. RLS scopes to the org."""
        result = await self._session.execute(
            select(Document)
            .where(Document.document_key == document_key)
            .options(selectinload(Document.tags), selectinload(Document.folder))
        )
        return result.scalar_one_or_none()

    async def list_for_folders(
        self,
        folder_ids: list[uuid.UUID] | None = None,
        *,
        include_unfiled: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Document], int]:
        """List documents, optionally scoped to a set of folders.

        ``folder_ids=None`` means "no folder restriction" (return everything
        the current RLS tenant scope allows). When ``folder_ids`` is a concrete
        list, only documents in those folders match — but note that a document
        with ``folder_id IS NULL`` (an "unfiled" doc) never satisfies an
        ``IN (...)`` predicate. ``include_unfiled=True`` additively surfaces
        those unfiled documents so they are not silently invisible; without it,
        docs created without a folder would never appear in any list.
        """
        query = select(Document).options(selectinload(Document.tags))

        if folder_ids is not None:
            folder_filter = Document.folder_id.in_(folder_ids)
            if include_unfiled:
                folder_filter = or_(folder_filter, Document.folder_id.is_(None))
            query = query.where(folder_filter)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self._session.execute(count_query)).scalar_one()

        query = query.order_by(Document.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def create(
        self,
        *,
        title: str,
        org_id: uuid.UUID,
        text: str | None = None,
        description: str | None = None,
        folder_id: uuid.UUID | None = None,
        uploaded_by_id: uuid.UUID | None = None,
        use_knowledge_graph: bool | None = None,
        metadata: dict[str, Any] | None = None,
        tag_ids: list[uuid.UUID] | None = None,
    ) -> Document:
        doc = Document(
            title=title,
            org_id=org_id,
            text=text,
            description=description,
            folder_id=folder_id,
            uploaded_by_id=uploaded_by_id,
            use_knowledge_graph=use_knowledge_graph,
            metadata_=metadata or {},
            document_key=str(uuid.uuid4()),
        )
        self._session.add(doc)
        await self._session.flush()

        if tag_ids:
            tag_result = await self._session.execute(select(Tag).where(Tag.id.in_(tag_ids)))
            doc.tags = list(tag_result.scalars().all())
            await self._session.flush()

        return doc

    async def update_status(
        self,
        document_id: uuid.UUID,
        *,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> Document | None:
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.processing_status = status
        if details is not None:
            doc.processing_details = details
        await self._session.flush()
        return doc

    async def delete(self, document_id: uuid.UUID) -> bool:
        doc = await self.get(document_id)
        if doc is None:
            return False
        await self._session.delete(doc)
        await self._session.flush()
        return True
