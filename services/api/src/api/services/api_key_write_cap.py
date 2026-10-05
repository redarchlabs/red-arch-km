"""The per-key daily cap on knowledge-base writes (``API_KEY_DOCUMENT_WRITES_PER_DAY``).

Every write may start an LLM-billed ingest, so each API key may add or replace a
bounded number of documents per day. ``POST /api/v1/knowledge/documents`` and the
``create_document`` tool of a run the key started share ONE counter, keyed here.
"""

from __future__ import annotations

import uuid

DOC_WRITE_WINDOW_SECONDS = 86_400


def doc_write_cap_key(api_key_id: uuid.UUID | None) -> str:
    """The rate-limit bucket for one key's document writes."""
    return f"docwrite:{api_key_id}"
