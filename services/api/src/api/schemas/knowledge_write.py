"""Public-API document write: ``POST /api/v1/knowledge/documents``.

The JSON form carries text content; the multipart form carries a file plus the
same fields as form values (``metadata`` as a JSON string). Both are normalised to
a :class:`~api.services.knowledge_write.KnowledgeDocumentInput` before the write
service runs, so every limit below applies to both.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Caller-chosen stable id ("proj-weekly-2026-W41", "proj:charter"). Restricted to a
# URL/log-safe alphabet; it is looked up, never interpolated into paths. Matched with
# fullmatch: ``re.match`` + ``$`` would accept a trailing newline.
_EXTERNAL_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,254}")
# Caller metadata is stored on the document and copied into every chunk/document
# payload in the index, so it is bounded: a flat object of scalars (or short lists
# of scalars), not a payload channel.
MAX_METADATA_BYTES = 8 * 1024
MAX_METADATA_KEYS = 50
MAX_METADATA_LIST = 50
# Fields the index filters, scopes or cites on. brain-api already makes them win
# over caller metadata (RESERVED_INGEST_METADATA_KEYS); rejecting them here gives
# the caller a clear 422 instead of a silently ignored key. Keep in sync.
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
_SCALARS = (str, int, float, bool, type(None))


def reject_nul(value: str, field: str) -> str:
    # PostgreSQL text/jsonb cannot store U+0000; refuse it here rather than 500 later.
    if "\x00" in value:
        msg = f"{field} must not contain NUL characters"
        raise ValueError(msg)
    return value


def validate_external_ref(value: str) -> str:
    if not _EXTERNAL_REF_RE.fullmatch(value):
        msg = (
            "external_ref must be 1-255 characters of letters, digits, '.', '_', ':' or '-', "
            "starting with a letter or digit"
        )
        raise ValueError(msg)
    return value


def _check_scalar(key: str, value: Any) -> None:
    if not isinstance(value, _SCALARS):
        msg = f"metadata[{key!r}] must be a string, number, boolean, null or a list of those"
        raise ValueError(msg)
    if isinstance(value, str):
        reject_nul(value, f"metadata[{key!r}]")


def reject_reserved_metadata_keys(value: dict[str, Any]) -> dict[str, Any]:
    """Raise ``ValueError`` naming every reserved key in ``value``; else return it.

    Shared by the public API (:func:`validate_metadata`) and the first-party
    document schemas, which keep their looser shape rules but must not let
    metadata name an index-owned field either.
    """
    reserved = sorted(set(value) & RESERVED_METADATA_KEYS)
    if reserved:
        msg = f"metadata may not set reserved field(s): {', '.join(reserved)}"
        raise ValueError(msg)
    return value


def validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > MAX_METADATA_KEYS:
        msg = f"metadata may have at most {MAX_METADATA_KEYS} keys"
        raise ValueError(msg)
    reject_reserved_metadata_keys(value)
    for key, item in value.items():
        reject_nul(key, "metadata key")
        if isinstance(item, list):
            if len(item) > MAX_METADATA_LIST:
                msg = f"metadata[{key!r}] may hold at most {MAX_METADATA_LIST} items"
                raise ValueError(msg)
            for element in item:
                _check_scalar(key, element)
        else:
            _check_scalar(key, item)
    if len(json.dumps(value, default=str).encode("utf-8")) > MAX_METADATA_BYTES:
        msg = f"metadata must serialise to at most {MAX_METADATA_BYTES} bytes"
        raise ValueError(msg)
    return value


def parse_metadata_field(raw: Any) -> dict[str, Any] | None:
    """A multipart ``metadata`` value (a JSON string) → a validated object."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, RecursionError) as exc:
            # RecursionError: deeply nested JSON must be a 422, not a 500.
            msg = "metadata must be a JSON object"
            raise ValueError(msg) from exc
    if not isinstance(raw, dict):
        msg = "metadata must be a JSON object"
        raise ValueError(msg)
    return validate_metadata(raw)


class KnowledgeDocumentJsonWrite(BaseModel):
    """JSON body: text content."""

    model_config = ConfigDict(extra="forbid")

    folder_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    external_ref: str | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return reject_nul(v, "title")

    @field_validator("content")
    @classmethod
    def _content(cls, v: str) -> str:
        return reject_nul(v, "content")

    @field_validator("external_ref")
    @classmethod
    def _ref(cls, v: str | None) -> str | None:
        return validate_external_ref(v) if v is not None else None

    @field_validator("metadata")
    @classmethod
    def _meta(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_metadata(v) if v is not None else None


class KnowledgeDocumentFormFields(BaseModel):
    """Multipart form fields (the file itself is validated separately)."""

    model_config = ConfigDict(extra="forbid")

    folder_id: uuid.UUID
    title: str | None = Field(default=None, min_length=1, max_length=255)
    external_ref: str | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("external_ref")
    @classmethod
    def _ref(cls, v: str | None) -> str | None:
        return validate_external_ref(v) if v is not None else None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        return reject_nul(v, "title") if v is not None else None

    @field_validator("metadata", mode="before")
    @classmethod
    def _parse_meta(cls, v: Any) -> Any:
        return parse_metadata_field(v)


WriteOutcome = Literal["created", "updated", "reprocessed", "metadata_updated", "unchanged"]


class KnowledgeDocumentWriteResult(BaseModel):
    """What the write did, and the document to poll (GET /knowledge/documents/{id})."""

    id: uuid.UUID
    document_key: str
    external_ref: str | None
    folder_id: uuid.UUID | None
    title: str
    processing_status: str
    content_hash: str | None
    # created: new document · updated: new content for an existing external_ref,
    # re-ingested · reprocessed: same content, previous ingest had failed, was
    # cancelled or is stuck queued, so it was re-run · metadata_updated: same
    # content, new title/metadata applied without re-ingest · unchanged: nothing done.
    outcome: WriteOutcome
