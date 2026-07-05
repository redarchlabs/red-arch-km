"""OpenAI vision OCR (the ``ai`` translation method).

Ported from KM v1 (``extract_text_with_ai`` / ``read_unligible_doc``): render
each PDF page to PNG, base64-encode, and ask an OpenAI vision model to
transcribe it to Markdown. Operates on in-memory bytes.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import TYPE_CHECKING

from pdf2image import convert_from_bytes
from PIL import Image

from worker.config import WorkerSettings

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)

_PROMPT = (
    "Transcribe this scanned page into clean Markdown. "
    "Preserve headings, lists, and tables (use Markdown tables). "
    "Include a page header like '## Page {page}'. "
    "If text is unclear, write '[illegible]'."
)


def _transcribe_png(client: OpenAI, model: str, png: bytes, page: int) -> str:
    """Send one PNG page to the vision model and return its Markdown."""
    b64 = base64.b64encode(png).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _PROMPT.format(page=page)},
                    {"type": "input_image", "image_url": data_url, "detail": "high"},
                ],
            }
        ],
    )
    return str(response.output_text)


def _image_to_png(data: bytes) -> bytes:
    """Normalise arbitrary raster bytes to PNG for the vision API."""
    with Image.open(io.BytesIO(data)) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()


def extract(data: bytes, filename: str, api_key: str, *, model: str | None = None) -> str:
    """Transcribe a PDF or image to Markdown via an OpenAI vision model.

    ``api_key`` is required (the caller resolves per-org vs central key first);
    an empty key raises so we never silently skip the paid path.
    """
    if not api_key:
        msg = "OpenAI API key is required for AI extraction"
        raise ValueError(msg)

    # Imported lazily so the module (and the extraction dispatcher) can be
    # imported without the openai package present — e.g. in the OCR-only path
    # and in unit tests that monkeypatch this function.
    from openai import OpenAI

    settings = WorkerSettings()
    resolved_model = model or settings.openai_ocr_model
    client = OpenAI(api_key=api_key)

    if filename.lower().endswith(".pdf"):
        # Cap pages so a many-page PDF can't OOM the worker or run up unbounded
        # per-page vision billing. Pages past the cap are skipped with a warning.
        max_pages = settings.max_ocr_pages
        images = convert_from_bytes(data, last_page=max_pages)
        if len(images) >= max_pages:
            logger.warning("PDF exceeds max_ocr_pages=%d; transcribing only the first %d pages", max_pages, max_pages)
        pages: list[str] = []
        for i, image in enumerate(images, start=1):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pages.append(_transcribe_png(client, resolved_model, buffer.getvalue(), i))
            logger.debug("AI transcribed PDF page %d/%d", i, len(images))
        return "\n\n".join(pages)

    return _transcribe_png(client, resolved_model, _image_to_png(data), 1)
