"""Legacy Word (.doc, Word 97-2003 binary) extraction via antiword.

.doc is a binary OLE format that mammoth (which handles the modern .docx XML)
cannot read. antiword is a tiny, well-established tool that extracts the text
with paragraph breaks preserved — good enough for these legacy files.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def extract(data: bytes) -> str:
    """Extract text from legacy .doc bytes using antiword."""
    # antiword reads from a path, not stdin, so spool to a temp file.
    with tempfile.NamedTemporaryFile(suffix=".doc") as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            result = subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["antiword", "-w", "0", tmp.name],  # noqa: S607 — resolved via PATH in the image
                capture_output=True,
                timeout=120,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.exception("antiword failed on .doc")
            msg = "Failed to extract text from the .doc file"
            raise ValueError(msg) from e
    return result.stdout.decode("utf-8", errors="replace")
