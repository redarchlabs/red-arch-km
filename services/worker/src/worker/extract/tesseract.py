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

logger = logging.getLogger(__name__)


def extract_pdf(data: bytes) -> str:
    """OCR every page of a PDF and return the concatenated text."""
    images = convert_from_bytes(data)
    pages: list[str] = []
    for i, image in enumerate(images, start=1):
        pages.append(pytesseract.image_to_string(image))
        logger.debug("Tesseract OCR'd PDF page %d/%d", i, len(images))
    return "\n".join(pages)


def extract_image(data: bytes) -> str:
    """OCR a single raster image and return its text."""
    with Image.open(io.BytesIO(data)) as image:
        return pytesseract.image_to_string(image)
