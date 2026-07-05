"""Tesseract OCR extraction (the default ``ocr`` translation method).

Operates on in-memory bytes downloaded from object storage — nothing touches
the local filesystem. Requires the ``tesseract`` binary and, for PDFs, the
``poppler`` utilities (via pdf2image).
"""

from __future__ import annotations

import io
import logging

import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

from worker.config import WorkerSettings

logger = logging.getLogger(__name__)


def extract_pdf(data: bytes) -> str:
    """OCR up to ``max_ocr_pages`` pages of a PDF and return concatenated text."""
    max_pages = WorkerSettings().max_ocr_pages
    # `last_page` caps rendering at poppler level so we never rasterize the whole
    # document into memory just to throw pages away.
    images = convert_from_bytes(data, last_page=max_pages)
    if len(images) >= max_pages:
        logger.warning("PDF exceeds max_ocr_pages=%d; OCR'ing only the first %d pages", max_pages, max_pages)
    pages: list[str] = []
    for i, image in enumerate(images, start=1):
        pages.append(pytesseract.image_to_string(image))
        logger.debug("Tesseract OCR'd PDF page %d/%d", i, len(images))
    return "\n".join(pages)


def extract_image(data: bytes) -> str:
    """OCR a single raster image and return its text."""
    with Image.open(io.BytesIO(data)) as image:
        return pytesseract.image_to_string(image)
