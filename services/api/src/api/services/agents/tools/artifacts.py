"""Documents that came into a work order, and documents that came out of it.

An agent could research, plan and report, but everything it produced was prose in
a diary entry — there was no way to hand back an artifact, and no way to find one
a person had handed in. The work order recorded what happened and not what came
of it.

``attach_document`` deliberately goes through the same path as ``create_document``
(validate folder → persist → commit → enqueue ingest) rather than writing a row of
its own: an attached report should be searchable, permissioned and reprocessable
like every other document in the org. The artifact row is a *link*, added on top.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from api import db_scope
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.repositories.work_order_artifacts import WorkOrderArtifactRepository
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec
from api.tasks.ingest import dispatch_ingest

logger = logging.getLogger(__name__)

# How much of a document a read hands back in one go. Enough for a spec or a
# report; past this the agent should search rather than swallow the whole thing.
_MAX_READ_CHARS = 20_000


def _no_work_order() -> dict[str, Any]:
    return {
        "error": (
            "This run is not attached to a work order, so it has no documents. "
            "Use create_document for a standalone document instead."
        )
    }


async def _attach_document(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return _no_work_order()
    title = str(args.get("title") or "").strip()
    text = str(args.get("content") or "").strip()
    if not title:
        return {"error": "'title' is required — name the document you are attaching."}
    if not text:
        return {"error": "'content' is required — an empty document is not an artifact."}

    doc_repo = DocumentRepository(ctx.session, ctx.org_id)
    folder_repo = FolderRepository(ctx.session, ctx.org_id)

    access_keys: list[int] = []
    tag_names: list[str] = []
    folder_id: uuid.UUID | None = None
    raw_folder = args.get("folder_id")
    if raw_folder:
        try:
            folder_id = uuid.UUID(str(raw_folder))
        except (ValueError, TypeError):
            return {"error": "folder_id is not a valid id"}
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
        metadata={"work_order_id": str(ctx.work_order_id)},
    )
    doc.size_bytes = len(text.encode("utf-8"))
    doc_id, doc_key = doc.id, doc.document_key

    await WorkOrderArtifactRepository(ctx.session, ctx.org_id).attach(
        ctx.work_order_id,
        doc_id,
        kind="output",
        filename=f"{title}.md",
        mime="text/markdown",
        size=doc.size_bytes,
    )

    # Commit before dispatching so the ingest worker can read the row, then
    # re-apply tenant scope: the commit ends the transaction and reverts every
    # SET LOCAL, and the caller keeps writing on this same session afterwards.
    await ctx.session.commit()
    await db_scope.enter_tenant(ctx.session, ctx.org_id)

    ingest = "queued"
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
                "use_knowledge_graph": True,
                "metadata": {"work_order_id": str(ctx.work_order_id)},
            }
        )
        doc.celery_task_id = task_id
    except Exception:  # noqa: BLE001 — a broker outage must not undo a committed attach
        logger.exception("attach_document %s: ingest enqueue failed; left PENDING", doc_id)
        ingest = "pending_enqueue_failed"

    return {
        "attached": True,
        "document_id": str(doc_id),
        "title": title,
        "ingest": ingest,
        "note": "Attached to this work order. A person can open it from the order's Documents.",
    }


async def _list_documents(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return _no_work_order()
    rows = await WorkOrderArtifactRepository(ctx.session, ctx.org_id).list_for(ctx.work_order_id)
    return {
        "documents": [
            {
                "artifact_id": str(artifact.id),
                "document_id": str(artifact.document_id) if artifact.document_id else None,
                # Which direction it went is the useful part: inputs are what you
                # were given, outputs are what has already been produced.
                "kind": artifact.kind,
                "filename": artifact.filename or (document.title if document else None),
                "title": document.title if document else None,
                "missing": document is None,
            }
            for artifact, document in rows
        ]
    }


async def _read_document(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return _no_work_order()
    raw = str(args.get("document_id") or "").strip()
    try:
        document_id = uuid.UUID(raw)
    except (ValueError, TypeError):
        return {"error": "document_id is not a valid id. Call list_work_order_documents first."}

    rows = await WorkOrderArtifactRepository(ctx.session, ctx.org_id).list_for(ctx.work_order_id)
    # Only documents attached to THIS order. A run must not be able to read an
    # arbitrary org document by id through a work-order tool.
    match = next((doc for artifact, doc in rows if artifact.document_id == document_id and doc), None)
    if match is None:
        available = ", ".join(str(a.document_id) for a, _ in rows) or "none"
        return {"error": f"No document {raw} on this work order. Attached here: {available}."}

    text = match.text or ""
    return {
        "title": match.title,
        "text": text[:_MAX_READ_CHARS],
        "truncated": len(text) > _MAX_READ_CHARS,
    }


ATTACH_DOCUMENT = ToolSpec(
    name="attach_document",
    description=(
        "Attach a document you have written to this work order — a report, an analysis, a "
        "spec. This is how you hand back something durable rather than describing it in a "
        "diary entry. It becomes a searchable KM2 document a person can open."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "What the document is called."},
            "content": {"type": "string", "description": "The document itself, as Markdown."},
            "description": {"type": "string", "description": "One line on what it covers."},
            "folder_id": {"type": "string", "description": "Optional folder to file it in."},
        },
        "required": ["title", "content"],
    },
    category=Category.WRITE,
    handler=_attach_document,
    side_effecting=False,  # internal org content, not an outbound action
)

LIST_WORK_ORDER_DOCUMENTS = ToolSpec(
    name="list_work_order_documents",
    description=(
        "List the documents on this work order — what a person attached as input, and what "
        "agents have produced as output. Check this before starting: the spec you need may "
        "already be here."
    ),
    parameters={"type": "object", "properties": {}},
    category=Category.READ,
    handler=_list_documents,
    always_allowed=True,
)

READ_WORK_ORDER_DOCUMENT = ToolSpec(
    name="read_work_order_document",
    description=(
        "Read one document attached to this work order, whole. Use search_knowledge to find "
        "things across the org; use this when you know which attached document you want."
    ),
    parameters={
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "From list_work_order_documents."}},
        "required": ["document_id"],
    },
    category=Category.READ,
    handler=_read_document,
    always_allowed=True,
)


def artifact_tool_specs() -> list[ToolSpec]:
    return [ATTACH_DOCUMENT, LIST_WORK_ORDER_DOCUMENTS, READ_WORK_ORDER_DOCUMENT]
