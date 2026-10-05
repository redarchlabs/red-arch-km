"""Write a document through the public API: ``POST /api/v1/knowledge/documents``.

Reuses the internal pipeline the first-party documents router runs (validate the
folder → persist the row (and the original, for a file) → commit → enqueue the
worker ingest → record the task id). Ingest is asynchronous; the caller polls
``GET /api/v1/knowledge/documents/{id}``.

**Versioning by ``external_ref``** (unique per folder). Re-sending a ref replaces
that document's content in place — the same row, its old index purged, re-ingested
— rather than creating a duplicate. Identical content (SHA-256 of the bytes) is
not re-ingested: a changed title or metadata is applied on its own, and a
previous ingest that failed, was cancelled or is stuck queued is re-run. The row
is locked for the whole replace, so two writers cannot both re-ingest one ref.

**Who may write where.**

* A **scoped key** writes only into folders its own assignments may add to
  (:func:`~api.services.knowledge_visibility.can_add_to_folder`) and, when it lists
  folders, only inside those folders and their subfolders; a folder outside that
  set or one it cannot see is reported as not found. A ref whose document the key
  cannot see is a conflict with the same text as any other ref conflict, so
  nothing reveals that a hidden document exists. A scoped key is not a person:
  its documents have no ``uploaded_by``.
* An **org key** may write to any folder in its org — the same reach an org admin
  has. Its ``knowledge:write`` scope (never granted by a wildcard) is the gate.

The document's chunks and facts carry the folder's effective view masks (or the
document's own override on a replace), exactly like a document created in the UI.
Masks are always resolved BEFORE a commit: the commit ends the transaction and the
``SET LOCAL`` tenant scope with it, after which RLS would hide ancestor folders.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.auth.api_key import ApiKeyPrincipal
from api.config import Settings
from api.models.document import Document, Folder, ProcessingStatus
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.schemas.knowledge_write import WriteOutcome
from api.services.brain_client import BrainAPIClient
from api.services.document_ingest import EXTENSION_CONTENT_TYPES, persist_task_id
from api.services.index_tags import index_tags
from api.services.knowledge_visibility import can_add_to_folder, document_visible, folder_visible
from api.services.search_access import api_key_access_keys
from api.services.storage import StorageClient
from api.tasks.ingest import dispatch_extract_ingest, dispatch_ingest, dispatch_metadata_update

logger = logging.getLogger(__name__)

_REF_INDEX = "uq_doc_external_ref_per_folder"
# One text for every ref conflict, including "used by a document you cannot see".
REF_CONFLICT_DETAIL = "external_ref is already in use in this folder"
_IN_FLIGHT_DETAIL = "The previous version is still being processed; retry when its status is SUCCESS or FAILED"
_RETRYABLE = frozenset({ProcessingStatus.FAILED, ProcessingStatus.CANCELLED})
# PENDING with no task id is normal for the instant between commit and dispatch;
# past this grace it means the enqueue never happened. Any PENDING this old is stuck.
_PENDING_NO_TASK_GRACE = timedelta(seconds=60)
_PENDING_STALE_AFTER = timedelta(minutes=10)


class KnowledgeWriteError(Exception):
    """A refusal the router maps to an HTTP status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentInput:
    """A validated write. ``filename`` is None for text content (JSON form)."""

    folder_id: uuid.UUID
    title: str
    content: bytes
    filename: str | None = None
    content_type: str | None = None
    external_ref: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class WriteResult:
    outcome: WriteOutcome
    document: Document


def _constraint_name(exc: IntegrityError) -> str | None:
    """The violated constraint, from the driver error (asyncpg exposes it)."""
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
    return _REF_INDEX if f'"{_REF_INDEX}"' in str(orig) else None


def _may_contribute_to_document(doc: Document, masks: list[int] | None) -> bool:
    """A document's own contributor config, when set, narrows who may change it
    (on top of the folder's, already checked). Empty masks = no restriction."""
    if masks is None or doc.contributor_permissions_config is None:
        return True
    own = list(doc.contributor_permission_masks or [])
    return not own or bool(set(own) & set(masks))


def _is_stuck_pending(doc: Document, now: datetime) -> bool:
    if doc.processing_status != ProcessingStatus.PENDING:
        return False
    updated = doc.updated_at
    if updated is None:
        return doc.celery_task_id is None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    age = now - updated
    return age > _PENDING_STALE_AFTER or (doc.celery_task_id is None and age > _PENDING_NO_TASK_GRACE)


class KnowledgeWriteService:
    def __init__(self, session: AsyncSession, principal: ApiKeyPrincipal, settings: Settings) -> None:
        self._session = session
        self._principal = principal
        self._settings = settings
        self._org_id = principal.org_id
        self._docs = DocumentRepository(session, principal.org_id)
        self._folders = FolderRepository(session, principal.org_id)
        # None for an org key (org-wide); a scoped key's assignment masks otherwise.
        self._masks = api_key_access_keys(principal)
        # None = no folder limit; else the scoped key's expanded, visible folder set.
        self._folder_ids = principal.folder_ids

    async def upsert(self, data: KnowledgeDocumentInput) -> WriteResult:
        folder = await self._authorised_folder(data.folder_id)
        existing = (
            await self._docs.get_by_external_ref(folder.id, data.external_ref, for_update=True)
            if data.external_ref
            else None
        )
        if existing is None:
            return await self._create(folder, data)
        return await self._replace(existing, data)

    # ---- authorisation ----------------------------------------------------

    def _outside_folders(self, folder_id: uuid.UUID | None) -> bool:
        """Outside a folder-limited key's set (an unfiled document always is)."""
        return self._folder_ids is not None and (folder_id is None or folder_id not in self._folder_ids)

    async def _authorised_folder(self, folder_id: uuid.UUID) -> Folder:
        if self._outside_folders(folder_id):
            raise KnowledgeWriteError(404, "folder not found")
        folder = await self._folders.get(folder_id)
        # A folder the key cannot see is indistinguishable from a missing one.
        if folder is None or not await folder_visible(self._session, self._org_id, folder, self._masks):
            raise KnowledgeWriteError(404, "folder not found")
        if not await can_add_to_folder(self._session, self._org_id, folder, self._masks):
            raise KnowledgeWriteError(403, "This key's assignments may not add documents to this folder")
        return folder

    # ---- create -----------------------------------------------------------

    async def _create(self, folder: Folder, data: KnowledgeDocumentInput) -> WriteResult:
        is_file = data.filename is not None
        access_keys = await self._folders.effective_view_masks(folder)  # before the commit
        stored: str | None = None
        try:
            doc = await self._docs.create(
                title=data.title,
                text=None if is_file else data.content.decode("utf-8"),
                folder_id=folder.id,
                uploaded_by_id=None,  # a key is not a person
                metadata=data.metadata,
            )
            doc.external_ref = data.external_ref
            doc.content_hash = data.content_hash
            doc.size_bytes = len(data.content)
            if is_file:
                stored = self._store(doc, data, versioned=False)
                doc.document_url = stored
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            self._discard(stored)
            if _constraint_name(exc) == _REF_INDEX:
                # Two writers raced on the same ref; the unique index decided.
                raise KnowledgeWriteError(409, REF_CONFLICT_DETAIL) from exc
            raise
        except BaseException:
            self._discard(stored)
            raise

        await self._dispatch(doc, tags=index_tags([], folder.id), access_keys=access_keys)
        self._log("created", doc, data)
        return WriteResult(outcome="created", document=doc)

    # ---- replace (same folder + external_ref; row locked) -----------------

    async def _replace(self, doc: Document, data: KnowledgeDocumentInput) -> WriteResult:
        # The ref is looked up within the (already authorised) folder, so the folder
        # check is belt-and-braces; it is what refuses an unfiled document.
        if self._outside_folders(doc.folder_id) or not await document_visible(
            self._session, self._org_id, doc, self._masks
        ):
            raise KnowledgeWriteError(409, REF_CONFLICT_DETAIL)
        if not _may_contribute_to_document(doc, self._masks):
            raise KnowledgeWriteError(403, "This key's assignments may not change this document")

        now = datetime.now(UTC)
        retryable = doc.processing_status in _RETRYABLE or _is_stuck_pending(doc, now)
        if doc.content_hash == data.content_hash:
            if retryable:
                await self._requeue(doc, data, new_content=False)
                return WriteResult(outcome="reprocessed", document=doc)
            if self._metadata_changed(doc, data):
                await self._update_metadata_only(doc, data)
                return WriteResult(outcome="metadata_updated", document=doc)
            return WriteResult(outcome="unchanged", document=doc)

        if not retryable and doc.processing_status in (ProcessingStatus.PENDING, ProcessingStatus.PROCESSING):
            raise KnowledgeWriteError(409, _IN_FLIGHT_DETAIL)
        await self._requeue(doc, data, new_content=True)
        self._log("updated", doc, data)
        return WriteResult(outcome="updated", document=doc)

    @staticmethod
    def _metadata_changed(doc: Document, data: KnowledgeDocumentInput) -> bool:
        return doc.title != data.title or (data.metadata is not None and data.metadata != (doc.metadata_ or {}))

    async def _update_metadata_only(self, doc: Document, data: KnowledgeDocumentInput) -> None:
        """Same content, new title/metadata: no re-ingest, but the index's title
        (and tags/masks, recomputed) follow, as a UI PATCH does."""
        tags, access_keys = await self._scoping(doc)  # before the commit
        doc.title = data.title
        if data.metadata is not None:
            doc.metadata_ = data.metadata
        await self._session.commit()
        try:
            dispatch_metadata_update(
                {
                    "tenant_id": str(self._org_id),
                    "document_key": doc.document_key,
                    "new_tags": tags,
                    "new_access_keys": access_keys,
                    "title": doc.title,
                }
            )
        except Exception:
            # The row is right; the index title is briefly stale until the next touch.
            logger.exception("v1 write: metadata re-tag enqueue failed for document %s", doc.id)

    async def _requeue(self, doc: Document, data: KnowledgeDocumentInput, *, new_content: bool) -> None:
        """Purge the old index, commit the new version PENDING, enqueue the ingest.

        The purge runs first and is fatal: if brain-api cannot remove the old index
        nothing is changed (the lock is released by the rollback) and the caller is
        told to retry, instead of a re-ingest appending a second copy of every chunk
        and fact.
        """
        tags, access_keys = await self._scoping(doc)  # before the commit
        try:
            await BrainAPIClient(self._settings).remove_document(str(self._org_id), doc.document_key)
        except Exception as exc:
            logger.warning("v1 write: purge before re-ingest failed for %s: %s", doc.document_key, exc)
            raise KnowledgeWriteError(
                503, "The knowledge index is unavailable; nothing was changed. Retry later."
            ) from exc

        old_url = doc.document_url
        stored: str | None = None
        try:
            doc.title = data.title
            if data.metadata is not None:
                doc.metadata_ = data.metadata
            if new_content:
                if data.filename is not None:
                    stored = self._store(doc, data, versioned=True)
                    doc.document_url = stored
                    doc.text = None
                else:
                    doc.document_url = None
                    doc.text = data.content.decode("utf-8")
                doc.content_hash = data.content_hash
                doc.size_bytes = len(data.content)
            doc.processing_status = ProcessingStatus.PENDING
            doc.processing_details = {"stage": "queued"}
            doc.celery_task_id = None
            await self._session.commit()
        except BaseException:
            self._discard(stored)
            raise

        if new_content and old_url and old_url != doc.document_url:
            self._discard(old_url)
        await self._dispatch(doc, tags=tags, access_keys=access_keys)

    async def _scoping(self, doc: Document) -> tuple[list[str], list[int]]:
        """Same derivation as the internal reprocess: the document's own viewer
        override wins, else its folder's effective masks."""
        folder = await self._folders.get(doc.folder_id) if doc.folder_id is not None else None
        tags = index_tags([], folder.id if folder is not None else None)
        if doc.viewer_permissions_config is not None:
            return tags, list(doc.view_permission_masks or [])
        return tags, await self._folders.effective_view_masks(folder)

    # ---- storage + dispatch -----------------------------------------------

    def _store(self, doc: Document, data: KnowledgeDocumentInput, *, versioned: bool) -> str:
        """Store the original before the commit, so a storage outage rolls back the row.

        A replacement goes under a content-addressed prefix, never over the object
        the current version is served from: if the replace fails, that version is
        intact, and the superseded object is deleted only after the commit.
        """
        assert data.filename is not None
        prefix = f"{self._org_id}/{doc.document_key}"
        if versioned:
            prefix = f"{prefix}/{data.content_hash[:16]}"
        object_key = f"{prefix}/{data.filename}"
        ext = "." + data.filename.rsplit(".", 1)[-1].lower() if "." in data.filename else ""
        content_type = EXTENSION_CONTENT_TYPES.get(ext) or data.content_type or "application/octet-stream"
        try:
            StorageClient(self._settings).put_object(object_key, data.content, content_type)
        except Exception as exc:
            logger.exception("v1 write: failed to store original for org %s (%s)", self._org_id, data.filename)
            raise KnowledgeWriteError(502, "Failed to store the uploaded file") from exc
        return object_key

    def _discard(self, object_key: str | None) -> None:
        """Best-effort delete of an object no committed row points at."""
        if not object_key:
            return
        try:
            StorageClient(self._settings).delete_object(object_key)
        except Exception:
            logger.warning("v1 write: could not delete orphaned object %s", object_key)

    async def _dispatch(self, doc: Document, *, tags: list[str], access_keys: list[int]) -> None:
        """Enqueue the worker ingest and record its task id.

        A broker failure marks the (already committed) document FAILED and answers
        503, so the caller knows to resend — the same content then re-runs.
        """
        base: dict[str, Any] = {
            "document_id": str(doc.id),
            "tenant_id": str(self._org_id),
            "document_key": doc.document_key,
            "title": doc.title,
            "tags": tags,
            "access_keys": access_keys,
            "use_knowledge_graph": doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True,
            "metadata": doc.metadata_ or {},
        }
        try:
            if doc.document_url:
                task_id = dispatch_extract_ingest(
                    {
                        **base,
                        "document_url": doc.document_url,
                        "filename": doc.document_url.rsplit("/", 1)[-1],
                        "translation_method": "ocr",
                    }
                )
            else:
                task_id = dispatch_ingest({**base, "text": doc.text})
        except Exception as exc:
            logger.exception("v1 write: ingest enqueue failed for document %s; marking FAILED", doc.id)
            await self._mark_enqueue_failed(doc)
            raise KnowledgeWriteError(503, "The ingest queue is unavailable; resend this document to retry") from exc
        # Cancellation + job-log correlation; its own tenant-scoped transaction.
        await persist_task_id(self._session, self._org_id, doc, task_id)

    async def _mark_enqueue_failed(self, doc: Document) -> None:
        try:
            await db_scope.enter_tenant(self._session, self._org_id)
            await self._session.execute(
                update(Document)
                .where(Document.id == doc.id)
                .values(processing_status=ProcessingStatus.FAILED, processing_details={"stage": "enqueue_failed"})
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.exception("v1 write: could not mark document %s FAILED after enqueue failure", doc.id)

    def _log(self, what: str, doc: Document, data: KnowledgeDocumentInput) -> None:
        logger.info(
            "v1 write: %s document %s (ref=%s) in org %s via key %s",
            what,
            doc.id,
            data.external_ref,
            self._org_id,
            self._principal.api_key_id,
        )
