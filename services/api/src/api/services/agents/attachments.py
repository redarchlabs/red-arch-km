"""Turning attached documents into something a model can look at — once.

A pasted screenshot has to reach the model as an actual image, and this
deployment's object storage is not reachable from OpenAI's side, so an
``image_url`` part has to carry the bytes as a data URI. That works — the provider
hands ``messages`` to LiteLLM verbatim — but those messages do not stay in memory:
resume state lives in ``agent_runs.input`` and each turn lands in
``agent_run_steps``. A 2 MB screenshot is ~2.7 MB of base64 *per turn, per resume*.

So the rule here is **vision on arrival, text thereafter**:

* the turn the attachment arrives on carries the real image part;
* anything persisted or replayed carries :func:`placeholder` text instead.

The model sees the picture at the moment it matters, the transcript stays bounded,
and the OCR text the upload pipeline already extracts remains searchable for every
turn afterwards. Implementing the obvious version — attach the image and let it
ride in the message list — is what fills a JSONB column with a screenshot.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

# Bytes a single image may occupy before it is sent as text instead. Well under the
# upload cap on purpose: this bound is about what a *model* is willing to read and
# what a message can carry, not about what is safe to store.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# Images per turn. Past a handful a model attends to none of them, and each one is
# a data URI in the request body.
MAX_IMAGES_PER_TURN = 4

# What the vision path accepts. A PDF is a document, not an `image_url` part —
# the pipeline already extracts its text, which is the better representation.
VISION_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Attachment:
    """One document attached to a message."""

    document_id: str
    filename: str
    mime: str | None = None
    data: bytes | None = None  # loaded only when it is going to be shown

    @property
    def is_image(self) -> bool:
        return (self.mime or "").lower() in VISION_MIME_TYPES


def placeholder(attachment: Attachment) -> str:
    """How an attachment reads once the image itself is gone.

    Names the document so the agent can fetch it deliberately, and says what it
    was, so a later turn is not left wondering what the earlier one was looking at.
    """
    kind = "image" if attachment.is_image else "file"
    return f"[{kind}: {attachment.filename} — attached as document {attachment.document_id}]"


def as_text(attachments: list[Attachment]) -> str:
    """Every attachment as placeholder lines. The persisted form."""
    return "\n".join(placeholder(a) for a in attachments)


def build_user_turn(text: str, attachments: list[Attachment], *, vision: bool) -> dict[str, Any]:
    """The message to send *this* turn.

    Falls back to plain text — the same shape as before this existed — whenever
    there is nothing to show: no attachments, a model that cannot see, a file that
    is not an image, or one too large to send. A model that cannot do vision must
    get a working message, not an error.
    """
    shown = _showable(attachments) if vision else []
    if not shown:
        body = "\n".join(part for part in (text, as_text(attachments)) if part)
        return {"role": "user", "content": body}

    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    for attachment in shown:
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_uri(attachment)},
            }
        )
    # Anything not shown still has to be mentioned, or the agent cannot tell that a
    # fifth image or a PDF was attached at all.
    unshown = [a for a in attachments if a not in shown]
    if unshown:
        parts.append({"type": "text", "text": as_text(unshown)})
    return {"role": "user", "content": parts}


def persistable(message: dict[str, Any], attachments: list[Attachment]) -> dict[str, Any]:
    """The same turn, flattened to text, for storage and resume.

    Called on the message *before* it is written to resume state, so a run that
    parks and wakes days later does not carry a screenshot in its input column.
    """
    if not isinstance(message.get("content"), list):
        return message
    text = " ".join(
        part.get("text", "") for part in message["content"] if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    body = "\n".join(part for part in (text, as_text(attachments)) if part)
    return {**message, "content": body}


def flatten_content(content: Any) -> str:
    """Multimodal content as plain text.

    Used wherever content is treated as a string — transcript summarisation, the
    diary, anything rendered — so a list never reaches an f-string and shows up as
    a Python repr inside a summary the model later reads.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            out.append(str(part.get("text") or ""))
        elif part.get("type") == "image_url":
            out.append("[image]")
    return " ".join(p for p in out if p)


def _showable(attachments: list[Attachment]) -> list[Attachment]:
    out: list[Attachment] = []
    for attachment in attachments:
        if not attachment.is_image or not attachment.data:
            continue
        if len(attachment.data) > MAX_IMAGE_BYTES:
            continue
        out.append(attachment)
        if len(out) == MAX_IMAGES_PER_TURN:
            break
    return out


def _data_uri(attachment: Attachment) -> str:
    encoded = base64.b64encode(attachment.data or b"").decode("ascii")
    return f"data:{attachment.mime};base64,{encoded}"


# Extension -> mime, for the subset that can be shown. Derived from the filename
# because that is what the upload route treats as authoritative: a mislabelled
# Content-Type must not decide whether something is sent to a model as an image.
_IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def mime_for(filename: str) -> str | None:
    """The mime type of ``filename``, if it is one we can show a model."""
    lowered = (filename or "").lower()
    for extension, mime in _IMAGE_EXTENSIONS.items():
        if lowered.endswith(extension):
            return mime
    return None


async def load(session: Any, org_id: Any, document_ids: list[Any], settings: Any) -> list[Attachment]:
    """Fetch attachment metadata, and the bytes of anything showable.

    One loader for both runtimes: the worker builds a turn from ids on the run's
    input, the console from ids on the request, and neither should have its own
    idea of what a showable image is.
    """
    if not document_ids:
        return []
    import asyncio
    import uuid as _uuid

    from api.repositories.document import DocumentRepository
    from api.services.storage import StorageClient

    repo = DocumentRepository(session, org_id)
    storage = StorageClient(settings)
    out: list[Attachment] = []
    for value in list(document_ids)[:MAX_IMAGES_PER_TURN]:
        try:
            document = await repo.get(_uuid.UUID(str(value)))
        except (ValueError, TypeError):
            continue
        if document is None:
            continue
        # `document_url` is the object key for an uploaded file; a document made
        # from text has none and there is nothing to show for it.
        key = document.document_url or ""
        name = key.rsplit("/", 1)[-1] if key else document.title
        mime = mime_for(name)
        data: bytes | None = None
        if key and mime in VISION_MIME_TYPES:
            try:
                # Blocking MinIO client: off the event loop, so one slow read
                # cannot stall everything else the caller is driving.
                data = await asyncio.to_thread(storage.get_object, key)
            except Exception:  # noqa: BLE001 - a missing object costs the picture, not the run
                logger.warning("attachment %s could not be read from storage", document.id)
        out.append(Attachment(document_id=str(document.id), filename=name, mime=mime, data=data))
    return out
