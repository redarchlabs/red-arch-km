"""Org-scoped binary assets: 3D models, textures, and anything else a view has
to load that is data rather than code.

These exist because a model belongs to an ORG, not to the platform. Baking a
customer's geometry into `ui/public/` puts one tenant's content in everyone's
build, cannot differ between orgs, and cannot be updated without a redeploy.
Assets live in the same object store the org's logo already uses; the database
holds the key, and a view's config points at the serving route.

Path handling here is a security boundary. The path comes from the caller and
becomes part of a storage key, so a path that escapes the org's prefix reads
another tenant's files. Hence: normalize, then reject anything that is not a
plain relative path — before it is ever joined to the prefix.
"""

from __future__ import annotations

import posixpath
import re
import urllib.parse
import uuid

# Anonymous share links may read only what was deliberately put here. Sharing a
# view must not become read access to everything the org has uploaded, and an
# explicit prefix is the same opt-in shape the view share itself uses.
PUBLIC_PREFIX = "public"

MAX_PATH = 300

# Deliberately narrow: the characters a file name needs and nothing that has a
# second meaning to a path parser or a URL.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AssetError(ValueError):
    """A path that must not be turned into a storage key."""


def normalize_asset_path(path: str) -> str:
    """Return a safe relative path, or raise.

    Percent-encoding is decoded FIRST: `%2e%2e/x` is `../x` by the time anything
    downstream resolves it, so checking the raw form would pass a traversal
    through untouched.
    """
    if not isinstance(path, str) or not path.strip():
        raise AssetError("asset path is required")

    decoded = urllib.parse.unquote(path)
    if len(decoded) > MAX_PATH:
        raise AssetError(f"asset path too long (max {MAX_PATH})")
    if any(c in decoded for c in ("\\", "\x00", "\n", "\r")):
        raise AssetError("asset path contains an illegal character")

    cleaned = decoded.strip()
    if cleaned.endswith("/"):
        # A trailing slash asks for a directory. Coercing it into a file name
        # would answer a question the caller did not ask.
        raise AssetError("asset path must name a file, not a directory")
    cleaned = cleaned.lstrip("/")
    # Collapse duplicate separators without resolving anything — `posixpath.normpath`
    # would silently RESOLVE `..` into a shorter path rather than reject it, which
    # is exactly the outcome this must not have.
    segments = [s for s in cleaned.split("/") if s != ""]
    if not segments:
        raise AssetError("asset path is required")
    for seg in segments:
        if seg in (".", ".."):
            raise AssetError("asset path may not traverse directories")
        if not _SEGMENT.match(seg):
            raise AssetError(f"illegal path segment {seg!r}")

    return posixpath.join(*segments)


def asset_key(org_id: uuid.UUID, path: str) -> str:
    """The object-store key for one org's asset."""
    return f"{org_id}/assets/{normalize_asset_path(path)}"


def is_public_path(path: str) -> bool:
    """Whether an anonymous share link may read this asset.

    Matched on the first path SEGMENT, so `publicish/x` does not qualify by
    sharing a prefix with `public/`.
    """
    try:
        normalized = normalize_asset_path(path)
    except AssetError:
        return False
    return normalized.split("/", 1)[0] == PUBLIC_PREFIX


# What a browser is willing to treat as the thing it asked for. An asset is
# served back with a declared type, so this is also what stops an upload being
# stored as one kind of file and served as another.
CONTENT_TYPES: dict[str, str] = {
    "stl": "model/stl",
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "obj": "text/plain",
    "mtl": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "ktx2": "image/ktx2",
    "bin": "application/octet-stream",
}


def content_type_for(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in CONTENT_TYPES:
        raise AssetError(f"unsupported asset type {ext!r}; expected one of {', '.join(sorted(CONTENT_TYPES))}")
    return CONTENT_TYPES[ext]
