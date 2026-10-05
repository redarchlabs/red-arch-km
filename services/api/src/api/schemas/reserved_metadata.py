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
``create_document`` tool) through :func:`reject_reserved_metadata_keys`, and bundle
import drops them with :func:`strip_reserved_metadata_keys`.
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


def strip_reserved_metadata_keys(value: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """A new dict of ``value`` without reserved keys, and the keys dropped (sorted).

    For inputs that must be accepted rather than refused (a migration bundle
    exported before the rule existed). ``value`` is not modified.
    """
    kept = {k: v for k, v in (value or {}).items() if k not in RESERVED_METADATA_KEYS}
    return kept, reserved_metadata_keys(value)
