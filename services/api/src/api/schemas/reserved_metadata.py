"""Document metadata keys that belong to the index, not the caller.

Caller metadata is stored on the document and copied into every chunk/document
payload in the index. These are the payload fields retrieval filters, scopes or
cites on: ``{"access_keys": [0]}`` would make restricted content public, and
``document_key`` / ``tenant_id`` / ``tags`` / ``type`` would re-scope it.

brain-api already makes its own fields win at ingest (``RESERVED_INGEST_METADATA_KEYS``
in ``brain_api.services.ingest_service`` and ``reservedIngestMetadataKeys`` in
``brain-api-go/internal/pipeline/metadata.go``). The API refuses them up front so a
caller gets a clear 422 instead of a silently ignored key — every write path
(first-party ``/api/documents``, ``/api/v1/knowledge/documents``, the agent
``create_document`` tool) through :func:`reject_reserved_metadata_keys`, except a
first-party PATCH, which refuses only keys it adds or changes
(:func:`changed_reserved_metadata_keys`); bundle import drops them with
:func:`strip_reserved_metadata_keys`.
``test_reserved_metadata_parity.py`` keeps the three lists equal.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESERVED_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "access_keys",
        "tenant_id",
        "tags",
        "document_key",
        "document_id",
        "document_title",
        "type",
        "text",
        "summary",
        "summary_tree",
        "section",
        "chunk_order",
    }
)


def reserved_metadata_keys(value: Mapping[str, Any] | None) -> list[str]:
    """The reserved keys present in ``value``, sorted (empty when none)."""
    return sorted(set(value or {}) & RESERVED_METADATA_KEYS)


def reject_reserved_metadata_keys(value: dict[str, Any]) -> dict[str, Any]:
    """Return ``value`` unchanged, or raise ``ValueError`` naming every reserved key
    in it (a pydantic validator turns that into a 422)."""
    reserved = reserved_metadata_keys(value)
    if reserved:
        msg = f"metadata may not set reserved field(s): {', '.join(reserved)}"
        raise ValueError(msg)
    return value


def changed_reserved_metadata_keys(sent: Mapping[str, Any] | None, stored: Mapping[str, Any] | None) -> list[str]:
    """Reserved keys in ``sent`` that ``stored`` lacks or holds a different value for.

    For a partial update of metadata saved before the rule existed: a client that
    re-sends what it loaded (say, the document's own ``document_key``) is not
    refused for it, but it cannot add a reserved key or change one. Unchanged
    legacy keys stay in the stored metadata and are dropped by brain-api at
    ingest like any other reserved key.
    """
    stored = stored or {}
    return [k for k in reserved_metadata_keys(sent) if k not in stored or stored[k] != (sent or {})[k]]


def strip_reserved_metadata_keys(value: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """A new dict of ``value`` without reserved keys, and the keys dropped (sorted).

    For inputs that must be accepted rather than refused (a migration bundle
    exported before the rule existed). ``value`` is not modified.
    """
    kept = {k: v for k, v in (value or {}).items() if k not in RESERVED_METADATA_KEYS}
    return kept, reserved_metadata_keys(value)
