"""Org portability endpoints: export the whole org to a JSON bundle, and import
a bundle into the current org.

Both are org-admin only and operate on the caller's current org (``X-Org-ID``).
Everything runs on the privileged session; the underlying repositories scope
every read/write to ``org_id`` explicitly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.services.migration import (
    BUNDLE_FORMAT_VERSION,
    BUNDLE_KIND,
    CollisionStrategy,
    ImportSummary,
    MigrationExporter,
    MigrationImporter,
)
from api.services.migration.bundle import Selection

router = APIRouter()


class ExportRequest(BaseModel):
    """What to export. ``selection`` maps a resource type to the ids to include;
    a type omitted from the selection exports all of it. Omit ``selection``
    entirely to export the whole org."""

    selection: Selection | None = None
    include_records: bool = True
    include_documents: bool = True


def _parse_selection(raw: str | None) -> Selection | None:
    """Parse the multipart ``selection`` field (a JSON object of type -> id[])."""
    if raw is None or raw.strip() == "":
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid selection: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="selection must be a JSON object")
    return parsed

# Upload guard: a bundle is JSON text (documents can be large, but a multi-hundred-MB
# upload is almost certainly a mistake or abuse).
MAX_BUNDLE_BYTES = 256 * 1024 * 1024


@router.get("/manifest")
async def export_manifest(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """A lightweight index (ids + names, no record rows or document text) of every
    selectable object in the current org, so the client can offer search +
    checkboxes before an export."""
    return await MigrationExporter(session, ctx.org_id).manifest()


@router.post("/export")
async def export_org(
    body: ExportRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Serialize the current org to a downloadable JSON bundle, narrowed to
    ``body.selection`` when provided. Secrets (connection credentials, webhook
    tokens) are never included."""
    bundle = await MigrationExporter(session, ctx.org_id).export(
        selection=body.selection,
        include_records=body.include_records,
        include_documents=body.include_documents,
    )
    bundle["exported_at"] = datetime.now(UTC).isoformat()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"km2-export-{stamp}.json"
    return JSONResponse(
        content=bundle,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _read_bundle(file: UploadFile) -> dict[str, Any]:
    raw = await file.read(MAX_BUNDLE_BYTES + 1)
    if len(raw) > MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"bundle exceeds the {MAX_BUNDLE_BYTES // (1024 * 1024)} MB limit",
        )
    try:
        bundle = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"not a valid JSON bundle: {exc}"
        ) from exc
    if not isinstance(bundle, dict) or bundle.get("kind") != BUNDLE_KIND:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file is not a KM2 migration bundle",
        )
    version = bundle.get("format_version")
    if version != BUNDLE_FORMAT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported bundle format_version {version!r} (expected {BUNDLE_FORMAT_VERSION})",
        )
    return bundle


@router.post("/import", response_model=ImportSummary)
async def import_org(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile,
    strategy: Annotated[CollisionStrategy, Query()] = CollisionStrategy.SKIP,
    dry_run: Annotated[bool, Query()] = False,
    selection: Annotated[str | None, Form()] = None,
) -> ImportSummary:
    """Rebuild an exported bundle into the current org.

    ``strategy`` decides what happens on a name/slug collision (skip / overwrite /
    rename). ``dry_run=true`` runs the whole import inside a transaction that is
    rolled back, returning the summary without persisting anything. ``selection``
    (a JSON object of resource-type -> id[]) narrows the import to chosen objects;
    omit it to import everything in the bundle.
    """
    bundle = await _read_bundle(file)
    parsed_selection = _parse_selection(selection)
    importer = MigrationImporter(session, ctx.org_id, settings)
    try:
        summary = await importer.import_bundle(
            bundle, strategy, dry_run=dry_run, selection=parsed_selection
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean 400 instead of a 500
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"import failed: {exc}"
        ) from exc

    if dry_run:
        # Undo everything; get_db would otherwise commit on a clean return.
        await session.rollback()
        return summary

    # Commit the rebuilt graph, THEN enqueue document ingestion so the worker can
    # read the committed rows (mirrors POST /documents).
    await session.commit()
    await importer.dispatch_pending_ingests(summary)
    return summary
