"""Document tools — let an agent write to the knowledge base.

The single write tool ``create_document`` is what powers two core company loops:
capturing a research **report** (paired with a ``research_item`` record) and
writing the daily **briefing** — both as markdown documents that are ingested
into RAG, so the whole org can retrieve them later via ``search_knowledge``.

It mirrors the first-party ``POST /documents`` router exactly (validate folder →
create row → commit → enqueue ingest), using the run's ``actor_user_id`` as the
uploader (nullable — a scheduled run or a run started through an API key has no
human actor, which is fine). A run started through an API key needs the key to
still hold ``knowledge:write`` (org keys too), counts against the key's daily
document-write cap (``API_KEY_DOCUMENT_WRITES_PER_DAY``, the same counter as
``POST /api/v1/knowledge/documents``), and — for a scoped key — writes only inside
that key's folders (see ``tools/key_scope.py``).

Governance: category ``WRITE`` (operator-kind + ``records_write`` grant). Writing
an internal KB document is **not** an external egress, so ``side_effecting`` is
False and a high-touch org does not gate it behind approval.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from api import db_scope
from api.dependencies import get_redis_client
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.schemas.reserved_metadata import reject_reserved_metadata_keys
from api.services.agents.tools.key_scope import RunKeyRefused, run_key_scope
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec
from api.services.api_key_scopes import has_scope
from api.services.api_key_write_cap import DOC_WRITE_WINDOW_SECONDS, doc_write_cap_key
from api.services.api_rate_limit import check_rate_limit, peek_rate_limit
from api.services.index_tags import index_tags
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
    metadata = args.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return {"error": "metadata must be an object"}
    try:
        # The index's own fields (masks, scope, identity); brain-api ignores them in
        # metadata anyway, but say so rather than drop them silently.
        reject_reserved_metadata_keys(metadata or {})
    except ValueError as exc:
        return {"error": str(exc)}
    text = args.get("text")
    text = str(text) if text is not None else None

    doc_repo = DocumentRepository(ctx.session, ctx.org_id)
    folder_repo = FolderRepository(ctx.session, ctx.org_id)

    # Validate the folder up front so a bad id fails cleanly (and scope the
    # ingest to the folder's view masks, exactly like the REST create path).
    access_keys: list[int] = []
    folder_id = _parse_id(args.get("folder_id")) if args.get("folder_id") else None
    folder = await folder_repo.get(folder_id) if folder_id is not None else None
    if folder_id is not None and folder is None:
        return {"error": "folder_id does not exist in this organization"}
    via_api_key = bool(getattr(ctx, "via_api_key", False))
    if via_api_key:
        refusal = await key_run_write_refusal(ctx, folder)
        if refusal is not None:
            return {"error": refusal}
    if folder is not None:
        access_keys = await folder_repo.effective_view_masks(folder)
    # The folder tag is server-derived; no user tag may carry its prefix.
    tag_names = index_tags([], folder.id if folder is not None else None)

    doc = await doc_repo.create(
        title=title,
        text=text,
        description=args.get("description"),
        folder_id=folder_id,
        # A key is not a person: a key-started run's documents have no uploader.
        uploaded_by_id=None if via_api_key else ctx.actor_user_id,
        use_knowledge_graph=args.get("use_knowledge_graph"),
        metadata=metadata or {},
    )
    doc.size_bytes = len(text.encode("utf-8")) if text else None

    # Capture what we need BEFORE committing (the instance may expire on commit).
    doc_id = doc.id
    doc_key = doc.document_key
    use_kg = doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True
    metadata = doc.metadata_ or {}

    # Commit before dispatching so the ingest worker can read the row (mirrors the
    # router). The commit ends the transaction and reverts all SET LOCAL scope, so
    # re-apply the tenant scope before returning — the caller (run_executor /
    # console) keeps writing (tool_result step, finalize) on this same session and
    # would otherwise hit RLS unscoped. See api/db_scope.py.
    await ctx.session.commit()
    await db_scope.enter_tenant(ctx.session, ctx.org_id)
    if via_api_key:
        await count_key_run_write(ctx)

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


async def key_run_write_refusal(ctx: ToolContext, folder: Any) -> str | None:
    """Why a run started through an API key may not write a document into
    ``folder`` (``None`` = unfiled) now, or ``None`` when it may.

    The one gate for every agent tool that adds a knowledge-base document
    (``create_document``, ``attach_document``): the key's scope and folder limits,
    then the key's daily write cap. Call it before persisting anything, and
    :func:`count_key_run_write` after the commit.
    """
    return await _key_write_refusal(ctx, folder) or await _daily_cap_refusal(ctx)


async def _key_write_refusal(ctx: ToolContext, folder: Any) -> str | None:
    """Re-check the run's API key at write time (it may have been revoked mid-run).

    Any key must still be valid and hold ``knowledge:write`` — the scope the REST
    write requires. A scoped key's run must also name a folder (unfiled documents
    bypass folder permissions), keep inside the key's folder set, and have masks
    that may add to that folder; an org key writes with org-wide reach, as the REST
    write does.
    """
    from api.services.knowledge_visibility import can_add_to_folder

    try:
        key_scope = await run_key_scope(ctx)
    except RunKeyRefused as exc:
        return str(exc)
    if key_scope is None:
        return None
    if not has_scope(key_scope.scopes, "knowledge:write"):
        return "The API key that started this run does not (or no longer) hold knowledge:write."
    if not key_scope.scoped:
        return None
    if folder is None:
        return "A folder_id is required: this run may only file into the API key's folders."
    if key_scope.folder_ids is not None and folder.id not in key_scope.folder_ids:
        return "That folder is outside the folders the API key that started this run may use."
    masks = list(key_scope.masks or ())
    if not masks or not await can_add_to_folder(ctx.session, ctx.org_id, folder, masks):
        return "The API key that started this run may not add documents to that folder."
    return None


async def _daily_cap_refusal(ctx: ToolContext) -> str | None:
    """Every write may start an LLM-billed ingest, so a key's writes are capped per
    day — agent writes and REST writes share one counter. Peeked (not counted)
    before the write; fail-open on a Redis outage, like the REST path."""
    result = await peek_rate_limit(
        get_redis_client(ctx.settings),
        doc_write_cap_key(ctx.api_key_id),
        limit=ctx.settings.api_key_document_writes_per_day,
        window_seconds=DOC_WRITE_WINDOW_SECONDS,
    )
    if result.allowed:
        return None
    return "The API key that started this run has reached its daily document-write limit; try again tomorrow."


async def count_key_run_write(ctx: ToolContext) -> None:
    """Count a committed write against the key's daily cap."""
    await check_rate_limit(
        get_redis_client(ctx.settings),
        doc_write_cap_key(ctx.api_key_id),
        limit=ctx.settings.api_key_document_writes_per_day,
        window_seconds=DOC_WRITE_WINDOW_SECONDS,
    )


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
