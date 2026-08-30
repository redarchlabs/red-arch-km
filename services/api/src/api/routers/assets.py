"""Org-scoped binary assets — upload, serve, list, delete.

A 3D model, a texture, a floor plan: content a view has to load that belongs to
one ORG rather than to the platform. See `api.services.assets` for why these are
not files in the repo.

Two ways in, deliberately asymmetric:

* the authenticated routes are org-scoped through the usual dependency, so a
  member reads their org's assets and only an admin writes them;
* the public route is keyed by a VIEW SHARE TOKEN and serves only assets under
  the ``public/`` prefix. It mirrors the branded-logo route exactly — same rate
  limit, same lifetime as the link — because an anonymous kiosk still has to be
  able to draw the model on the page it was given.

The public prefix is the whole access-control story for anonymous readers: a
share link must not become a way to enumerate everything an org has uploaded, so
what it can read is opted into by where it was put.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.config import Settings, get_settings
from api.services.assets import (
    AssetError,
    asset_key,
    content_type_for,
    is_public_path,
    normalize_asset_path,
)
from api.services.storage import StorageClient

router = APIRouter()

# Generous for a model, small enough that the upload path can stay in memory —
# the same trade the logo upload makes.
MAX_ASSET_BYTES = 25 * 1024 * 1024

# A model is immutable at its path (a changed model gets a new upload), so it is
# worth caching hard. Private: an authenticated asset must not land in a shared
# proxy cache where the next tenant could be served it.
_AUTH_CACHE = "private, max-age=3600"
_PUBLIC_CACHE = "public, max-age=3600"


def _bad(exc: AssetError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.put("/{path:path}")
async def upload_asset(
    path: str,
    file: UploadFile,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str | int]:
    """Store one asset at ``path`` for the caller's org, replacing any previous.

    The content type is derived from the PATH, not from the upload's own header:
    a client-declared type would let a file be stored as one thing and served as
    another, which is how an image endpoint ends up serving script.
    """
    try:
        safe = normalize_asset_path(path)
        content_type = content_type_for(safe)
    except AssetError as exc:
        raise _bad(exc) from None

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload")
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Asset exceeds {MAX_ASSET_BYTES // (1024 * 1024)}MB",
        )

    StorageClient(settings).put_object(asset_key(ctx.org_id, safe), data, content_type)
    return {"path": safe, "bytes": len(data), "content_type": content_type}


@router.get("/{path:path}")
async def get_asset(
    path: str,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Serve one of the caller's org's assets."""
    try:
        safe = normalize_asset_path(path)
        content_type = content_type_for(safe)
    except AssetError as exc:
        raise _bad(exc) from None
    return _serve(settings, asset_key(ctx.org_id, safe), content_type, _AUTH_CACHE)


@router.delete("/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    path: str,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        safe = normalize_asset_path(path)
    except AssetError as exc:
        raise _bad(exc) from None
    StorageClient(settings).delete_object(asset_key(ctx.org_id, safe))


def _serve(settings: Settings, key: str, content_type: str, cache: str) -> Response:
    try:
        data = StorageClient(settings).get_object(key)
    except Exception:
        # Any storage miss is a 404 to the caller: distinguishing "absent" from
        # "unreadable" would tell an anonymous visitor which keys exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found") from None
    return Response(content=data, media_type=content_type, headers={"Cache-Control": cache})


def public_asset_response(settings: Settings, org_id, path: str) -> Response:
    """Serve a `public/` asset on behalf of a share link.

    Lives here rather than in the views router so the prefix rule and the
    serving path cannot drift apart from each other.
    """
    try:
        safe = normalize_asset_path(path)
        content_type = content_type_for(safe)
    except AssetError as exc:
        raise _bad(exc) from None
    if not is_public_path(safe):
        # Not 403: telling an anonymous caller that a key exists but is private
        # is itself the enumeration this prefix exists to prevent.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return _serve(settings, asset_key(org_id, safe), content_type, _PUBLIC_CACHE)
