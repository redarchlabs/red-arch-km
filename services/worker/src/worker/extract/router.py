"""Extraction dispatcher: pick the extractor by file extension + method.

Keyed on the filename extension and the ``translation_method`` chosen at upload
time (``ocr`` = Tesseract, ``ai`` = OpenAI vision). Plain-text files bypass OCR
entirely and are decoded directly regardless of method.
"""

from __future__ import annotations

import logging
import os

from worker.extract import docx, openai_vision, tesseract

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md"})
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
)
PDF_EXTENSION = ".pdf"
DOCX_EXTENSION = ".docx"


def extract_text(data: bytes, filename: str, method: str, openai_key: str | None) -> str:
    """Extract text from ``data`` given its ``filename`` and ``method``.

    - ``.txt`` / ``.md`` → decoded directly (no OCR, method ignored).
    - ``.docx`` → converted to Markdown (no OCR, method ignored).
    - ``method == "ai"`` → OpenAI vision on PDFs/images.
    - otherwise → Tesseract OCR on PDFs/images.

    Raises ``ValueError`` for unsupported extensions or a missing key when the
    AI path needs one.
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext in TEXT_EXTENSIONS:
        return data.decode("utf-8", errors="ignore")

    if ext == DOCX_EXTENSION:
        logger.info("docx → Markdown extraction for %s", filename)
        return docx.extract(data)

    if ext != PDF_EXTENSION and ext not in IMAGE_EXTENSIONS:
        msg = f"Unsupported file type for extraction: {ext or filename!r}"
        raise ValueError(msg)

    if method == "ai":
        logger.info("AI (OpenAI vision) extraction for %s", filename)
        return openai_vision.extract(data, filename, openai_key or "")

    logger.info("Tesseract OCR extraction for %s", filename)
    if ext == PDF_EXTENSION:
        return tesseract.extract_pdf(data)
    return tesseract.extract_image(data)
