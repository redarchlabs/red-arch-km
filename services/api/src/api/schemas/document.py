"""Document and folder schemas."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _no_dots(name: str) -> str:
    if "." in name:
        msg = "Folder names cannot contain '.' (reserved for path separator)"
        raise ValueError(msg)
    return name


# ``document_key`` is the stable identifier shared with the vector store and
# emitted in chat/search citations. It ends up in URLs (``/documents/<key>``),
# object-storage keys (``<org_id>/<key>/<filename>``), and brain-api payloads,
# so it must be URL- and path-safe. A few literal sibling routes under
# ``/documents`` are reserved so a key can never shadow them.
_DOCUMENT_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_RESERVED_DOCUMENT_KEYS = frozenset({"upload", "search", "by-key"})


def validate_document_key(value: str) -> str:
    """Validate a caller-supplied document key; return it unchanged if valid.

    Raises ``ValueError`` (→ 422 at the schema boundary, translated to 400 for
    multipart form callers) for anything that would be unsafe in a URL/object
    key or that collides with a reserved route.
    """
    if not 1 <= len(value) <= 255:
        msg = "document_key must be 1–255 characters"
        raise ValueError(msg)
    if not _DOCUMENT_KEY_RE.match(value):
        msg = "document_key may contain only letters, digits, '.', '_' and '-'"
        raise ValueError(msg)
    if ".." in value:
        msg = "document_key cannot contain '..'"
        raise ValueError(msg)
    if value.lower() in _RESERVED_DOCUMENT_KEYS:
        msg = f"document_key '{value}' is reserved"
        raise ValueError(msg)
    return value


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    text: str | None = None
    folder_id: uuid.UUID | None = None
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    use_knowledge_graph: bool | None = None
    # Optional stable key shared with the vector store / citations. Defaults to
    # a random UUID when omitted (see DocumentRepository.create). Must be unique
    # within the org (enforced by uq_doc_key_per_org).
    document_key: str | None = None

    @field_validator("document_key")
    @classmethod
    def _validate_document_key(cls, v: str | None) -> str | None:
        return validate_document_key(v) if v is not None else None


class DocumentUpdate(BaseModel):
    """Partial update for a document. Only the provided fields are modified.

    `folder_id`, `tag_ids`, and `description` are tri-state (omitted / None /
    value); tests distinguish "omitted" from "explicit null" via
    `model_fields_set` to support clearing values.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    folder_id: uuid.UUID | None = None
    tag_ids: list[uuid.UUID] | None = None
    metadata: dict[str, Any] | None = None
    # Per-document permissions (independent of the folder; default from folder
    # at creation). Providing either recomputes that document's masks.
    viewer_permissions_config: list[dict[str, Any]] | None = None
    contributor_permissions_config: list[dict[str, Any]] | None = None


class DocumentContentUpdate(BaseModel):
    """Full-body replacement for a document's text (the Markdown editor).

    Distinct from ``DocumentUpdate`` (metadata only): persisting new text
    re-chunks and re-embeds the document, so it lives on its own endpoint. An
    empty string clears the body (and skips re-ingestion).
    """

    text: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    document_key: str
    processing_status: str
    # Ingest progress/result blob: {stage, percent} while PROCESSING,
    # {chunks, triplets} on SUCCESS, {error, ...} on FAILED.
    processing_details: dict[str, Any] | None = None
    folder_id: uuid.UUID | None
    org_id: uuid.UUID
    created_at: Any
    size_bytes: int | None = None
    viewer_permissions_config: list[dict[str, Any]] | None = None
    contributor_permissions_config: list[dict[str, Any]] | None = None


class JobLogEntry(BaseModel):
    """One structured line from an ingest job's Redis log list."""

    ts: str | None = None
    level: str | None = None
    stage: str | None = None
    message: str | None = None


class JobLogsRead(BaseModel):
    """An ingest job's log lines for a document, oldest first."""

    document_id: uuid.UUID
    events: list[JobLogEntry]


class UploadBatchRead(BaseModel):
    """Result of expanding an uploaded ``.zip`` into per-file documents.

    Distinguished from a single ``DocumentRead`` by the ``batch`` flag so the
    client can tell the two upload outcomes apart.
    """

    batch: bool = True
    created: int
    skipped: list[str]
    documents: list[DocumentRead]


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    viewer_permissions_config: list[dict[str, Any]] | None = None
    contributor_permissions_config: list[dict[str, Any]] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _no_dots(v)


class FolderUpdate(BaseModel):
    """Partial update — any field may be omitted, including parent_id.

    Note: parent_id is a `str | None` tagged union here; a literal ``None``
    reparents to root, while omitting the field leaves the parent unchanged.
    We distinguish "unset" from "set to null" via the `model_fields_set` API.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    viewer_permissions_config: list[dict[str, Any]] | None = None
    contributor_permissions_config: list[dict[str, Any]] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        return _no_dots(v) if v is not None else None


class FolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    parent_id: uuid.UUID | None
    dot_path: str
    order: int
    org_id: uuid.UUID
    created_at: Any = None
    viewer_permissions_config: list[dict[str, Any]] | None = None
    contributor_permissions_config: list[dict[str, Any]] | None = None


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
