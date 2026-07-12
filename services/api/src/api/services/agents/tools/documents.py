"""Document tools — let an agent write to the knowledge base.

The single write tool ``create_document`` is what powers two core company loops:
capturing a research **report** (paired with a ``research_item`` record) and
writing the daily **briefing** — both as markdown documents that are ingested
into RAG, so the whole org can retrieve them later via ``search_knowledge``.

It mirrors the first-party ``POST /documents`` router exactly (validate folder →
create row → commit → enqueue ingest), using the run's ``actor_user_id`` as the
uploader (nullable — a scheduled run has no human actor, which is fine).

Governance: category ``WRITE`` (operator-kind + ``records_write`` grant). Writing
an internal KB document is **not** an external egress, so ``side_effecting`` is
False and a high-touch org does not gate it behind approval.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec
from api.tasks.ingest import dispatch_ingest

logger = logging.getLogger(__name__)


def _parse_id(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _create_document(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        return {"error": "title is required"}
    text = args.get("text")
    text = str(text) if text is not None else None

    doc_repo = DocumentRepository(ctx.session, ctx.org_id)
    folder_repo = FolderRepository(ctx.session, ctx.org_id)

    # Validate the folder up front so a bad id fails cleanly (and scope the
    # ingest to the folder's view masks, exactly like the REST create path).
    access_keys: list[int] = []
    tag_names: list[str] = []
    folder_id = _parse_id(args.get("folder_id")) if args.get("folder_id") else None
    if folder_id is not None:
        folder = await folder_repo.get(folder_id)
        if folder is None:
            return {"error": "folder_id does not exist in this organization"}
        access_keys = await folder_repo.effective_view_masks(folder)
        tag_names.append(f"folder:{folder.id}")

    doc = await doc_repo.create(
        title=title,
        text=text,
        description=args.get("description"),
        folder_id=folder_id,
        uploaded_by_id=ctx.actor_user_id,
        use_knowledge_graph=args.get("use_knowledge_graph"),
        metadata=args.get("metadata") or {},
    )
    doc.size_bytes = len(text.encode("utf-8")) if text else None

    # Capture what we need BEFORE committing (the instance may expire on commit).
    doc_id = doc.id
    doc_key = doc.document_key
    use_kg = doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True
    metadata = doc.metadata_ or {}

    # Commit before dispatching so the ingest worker can read the row (mirrors the
    # router). The runtime session is privileged + explicit-org-scoped, so an
    # intra-turn commit is safe.
    await ctx.session.commit()

    ingest = "skipped_no_text"
    if text:
        try:
            task_id = dispatch_ingest(
                {
                    "document_id": str(doc_id),
                    "tenant_id": str(ctx.org_id),
                    "document_key": doc_key,
                    "title": title,
                    "text": text,
                    "tags": tag_names,
                    "access_keys": access_keys,
                    "use_knowledge_graph": use_kg,
                    "metadata": metadata,
                }
            )
            doc.celery_task_id = task_id  # flushed by the turn-end commit
            ingest = "queued"
        except Exception:  # noqa: BLE001 — a broker outage must not fail the (committed) create
            logger.exception("agent create_document %s: ingest enqueue failed; left PENDING", doc_id)
            ingest = "pending_enqueue_failed"

    return {"id": str(doc_id), "title": title, "folder_id": str(folder_id) if folder_id else None, "ingest": ingest}


CREATE_DOCUMENT = ToolSpec(
    name="create_document",
    description=(
        "Create a markdown document in the knowledge base (ingested for search). Use this to write "
        "a research report (into the Research folder, paired with a research_item record) or the "
        "daily briefing (into the Briefings folder). Pass 'text' as the full markdown body and "
        "'folder_id' to file it. Internal write — no human approval required."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Document title, e.g. '2026-07-12 — Daily Briefing'."},
            "text": {"type": "string", "description": "Full markdown body of the document."},
            "folder_id": {"type": "string", "description": "Folder id to file the document under (uuid)."},
            "description": {"type": "string", "description": "Optional one-line description."},
        },
        "required": ["title", "text"],
    },
    category=Category.WRITE,
    handler=_create_document,
)
