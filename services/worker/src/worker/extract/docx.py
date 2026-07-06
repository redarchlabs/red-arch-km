"""Word (.docx) extraction → Markdown.

Uses mammoth, which maps Word's semantic styles (headings, lists, bold, tables)
onto Markdown rather than flattening to plain text — so a .docx reads with its
structure intact, the same way uploaded Markdown does.
"""

from __future__ import annotations

import io
import logging

import mammoth

logger = logging.getLogger(__name__)


def extract(data: bytes) -> str:
    """Convert .docx bytes to Markdown text."""
    result = mammoth.convert_to_markdown(io.BytesIO(data))
    for message in result.messages:
        logger.debug("mammoth: %s", message)
    return result.value
