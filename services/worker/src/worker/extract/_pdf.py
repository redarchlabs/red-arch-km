"""Batched PDF page rendering shared by the OCR extractors.

Rendering a whole large PDF to images at once (``convert_from_bytes`` with just
``last_page``) holds every page bitmap in memory simultaneously — a ~300-page
book at 200 DPI is multiple GB and OOMs the worker. This helper renders a small
window of pages at a time and frees each bitmap right after the consumer uses
it, so peak memory is bounded by ``batch_size`` pages regardless of document
length. That's what lets ``max_ocr_pages`` be raised high enough for very large
documents (whole books) without a memory blowup.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from pdf2image import convert_from_bytes, pdfinfo_from_bytes
from PIL.Image import Image

logger = logging.getLogger(__name__)


def iter_pdf_pages(data: bytes, *, max_pages: int, batch_size: int) -> Iterator[tuple[int, Image]]:
    """Yield ``(page_number, image)`` for up to ``max_pages`` pages.

    Pages are rendered ``batch_size`` at a time (poppler ``first_page``/
    ``last_page`` windows) so only that many bitmaps are resident at once; each
    image is closed after it is yielded. Pages beyond ``max_pages`` are skipped
    with a warning (never silently). ``batch_size`` is floored at 1.
    """
    batch_size = max(1, batch_size)
    try:
        total = int(pdfinfo_from_bytes(data).get("Pages", 0))
    except Exception as exc:  # noqa: BLE001 — page count is best-effort; fall back to cap
        logger.warning("Could not read PDF page count (%s); capping at max_ocr_pages=%d", exc, max_pages)
        total = 0

    limit = min(total, max_pages) if total else max_pages
    if total and total > max_pages:
        logger.warning("PDF has %d pages; OCR'ing only the first %d (max_ocr_pages)", total, max_pages)

    start = 1
    while start <= limit:
        end = min(start + batch_size - 1, limit)
        images = convert_from_bytes(data, first_page=start, last_page=end)
        if not images:
            break
        for offset, image in enumerate(images):
            try:
                yield start + offset, image
            finally:
                image.close()  # free this page's bitmap before rendering the next batch
        if len(images) < (end - start + 1):
            break  # short batch → reached the real end of the document
        start = end + 1
