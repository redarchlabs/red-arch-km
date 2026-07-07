"""In-memory registry of in-flight background ingest jobs.

brain-api runs document ingestion in the background and returns 202 immediately,
so a very large document (chunk → embed → summarize → fact-extract over thousands
of chunks — minutes to over an hour) can't time out the caller's single HTTP
request. This registry records each job's state so the worker can poll for the
outcome.

It is deliberately process-local and best-effort: a brain-api restart drops it,
which the worker treats as a failed ingest and re-dispatches. Access is guarded
by a lock because the background task marks completion from a thread-pool thread
(`asyncio.to_thread`) while the status endpoint reads from the event loop.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal

JobState = Literal["running", "done", "failed"]

# Completed jobs linger this long so a slow poller still sees the outcome, then
# are evicted to bound memory. Longer than the worker's max poll wait.
_TTL_SECONDS = 2 * 60 * 60
# Hard cap on tracked jobs — a backstop against unbounded growth if pollers
# vanish. Oldest entries are evicted first.
_MAX_ENTRIES = 1000


@dataclass
class IngestJob:
    """The tracked state of one document's background ingest."""

    state: JobState
    chunks: int = 0
    triplets: int = 0
    error: str | None = None
    updated_at: float = field(default_factory=time.monotonic)


class IngestJobRegistry:
    """Thread-safe map of ``tenant/document_key`` → :class:`IngestJob`."""

    def __init__(self) -> None:
        self._jobs: dict[str, IngestJob] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(tenant_id: str, document_key: str) -> str:
        return f"{tenant_id}/{document_key}"

    def is_running(self, tenant_id: str, document_key: str) -> bool:
        with self._lock:
            job = self._jobs.get(self._key(tenant_id, document_key))
            return job is not None and job.state == "running"

    def mark_running(self, tenant_id: str, document_key: str) -> None:
        with self._lock:
            self._evict_locked()
            self._jobs[self._key(tenant_id, document_key)] = IngestJob(state="running")

    def mark_done(self, tenant_id: str, document_key: str, *, chunks: int, triplets: int) -> None:
        with self._lock:
            self._jobs[self._key(tenant_id, document_key)] = IngestJob(
                state="done", chunks=chunks, triplets=triplets
            )

    def mark_failed(self, tenant_id: str, document_key: str, error: str) -> None:
        with self._lock:
            self._jobs[self._key(tenant_id, document_key)] = IngestJob(state="failed", error=error[:500])

    def get(self, tenant_id: str, document_key: str) -> IngestJob | None:
        with self._lock:
            return self._jobs.get(self._key(tenant_id, document_key))

    def _evict_locked(self) -> None:
        """Drop TTL-expired entries, then cap total size. Caller holds the lock."""
        now = time.monotonic()
        for key in [k for k, j in self._jobs.items() if now - j.updated_at > _TTL_SECONDS]:
            del self._jobs[key]
        if len(self._jobs) >= _MAX_ENTRIES:
            oldest = sorted(self._jobs.items(), key=lambda kv: kv[1].updated_at)
            for key, _ in oldest[: len(self._jobs) - _MAX_ENTRIES + 1]:
                del self._jobs[key]


# Process-wide singleton — one registry per brain-api process.
_registry = IngestJobRegistry()


def get_ingest_jobs() -> IngestJobRegistry:
    """FastAPI dependency / accessor for the process-wide job registry."""
    return _registry
