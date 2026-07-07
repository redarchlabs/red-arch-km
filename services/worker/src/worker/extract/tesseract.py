"""Tesseract OCR extraction (the default ``ocr`` translation method).

Operates on in-memory bytes downloaded from object storage — nothing touches
the local filesystem. Requires the ``tesseract`` binary and, for PDFs, the
``poppler`` utilities (via pdf2image).
"""

from __future__ import annotations

import io
import logging

import pytesseract
from PIL import Image

from worker.config import WorkerSettings
from worker.extract._pdf import iter_pdf_pages

logger = logging.getLogger(__name__)


def extract_pdf(data: bytes) -> str:
    """OCR up to ``max_ocr_pages`` pages of a PDF and return concatenated text.

    Pages are rendered in bounded batches (see ``iter_pdf_pages``) so a very
    large document never rasterizes wholesale into memory.
    """
    settings = WorkerSettings()
    pages: list[str] = []
    for page_no, image in iter_pdf_pages(
        data, max_pages=settings.max_ocr_pages, batch_size=settings.ocr_page_batch_size
    ):
        pages.append(pytesseract.image_to_string(image))
        logger.debug("Tesseract OCR'd PDF page %d", page_no)
    return "\n".join(pages)


def extract_image(data: bytes) -> str:
    """OCR a single raster image and return its text."""
    with Image.open(io.BytesIO(data)) as image:
        return pytesseract.image_to_string(image)
