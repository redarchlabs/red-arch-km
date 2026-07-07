"""Unit tests for ingest job telemetry (worker/tasks/_job.py).

The Redis-backed helpers are exercised against a minimal fake broker, so no real
Redis is needed. report_status is patched to capture terminal reports.
"""

from __future__ import annotations

import json

import pytest
import worker.tasks._job as job_mod
from worker.tasks._job import (
    INGEST_CANCEL_KEY,
    JOB_LOG_KEY,
    STAGE_INGESTING,
    STAGE_PERCENT,
    IngestCancelled,
    is_cancelled,
    job_log,
    raise_if_cancelled,
    report_progress,
)


class _FakePipeline:
    def __init__(self, store: dict[str, list[str]]) -> None:
        self._store = store
        self._ops: list[tuple[str, tuple]] = []

    def rpush(self, key: str, value: str) -> None:
        self._ops.append(("rpush", (key, value)))

    def ltrim(self, key: str, start: int, end: int) -> None:
        self._ops.append(("ltrim", (key, start, end)))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", (key, ttl)))

    def execute(self) -> None:
        for op, args in self._ops:
            if op == "rpush":
                key, value = args
                self._store.setdefault(key, []).append(value)


class _FakeRedis:
    def __init__(self, store: dict[str, list[str]] | None = None, flags: set[str] | None = None) -> None:
        self._store = store if store is not None else {}
        self._flags = flags or set()

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self._store)

    def exists(self, key: str) -> int:
        return 1 if key in self._flags else 0

    def close(self) -> None: ...


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    redis = _FakeRedis()
    monkeypatch.setattr(job_mod, "_broker_redis", lambda: redis)
    return redis


def test_job_log_appends_structured_line(fake_redis: _FakeRedis) -> None:
    job_log("doc-9", "downloading original", stage="downloading")
    key = JOB_LOG_KEY.format(document_id="doc-9")
    stored = fake_redis._store[key]
    assert len(stored) == 1
    entry = json.loads(stored[0])
    assert entry["message"] == "downloading original"
    assert entry["stage"] == "downloading"
    assert entry["level"] == "info"


def test_job_log_noop_without_document_id(fake_redis: _FakeRedis) -> None:
    job_log("", "ignored")
    assert fake_redis._store == {}


def test_is_cancelled_reflects_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = "task-abc"
    redis = _FakeRedis(flags={INGEST_CANCEL_KEY.format(task_id=task_id)})
    monkeypatch.setattr(job_mod, "_broker_redis", lambda: redis)
    assert is_cancelled(task_id) is True
    assert is_cancelled("other-task") is False
    assert is_cancelled("") is False


def test_report_progress_reports_processing_with_stage_percent(
    monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis
) -> None:
    captured: list[tuple] = []
    monkeypatch.setattr(
        job_mod,
        "report_status",
        lambda _s, doc, tenant, status, details=None: captured.append((doc, status, details)),
    )
    report_progress(object(), "doc-1", "org-1", STAGE_INGESTING)
    assert captured == [("doc-1", "PROCESSING", {"stage": STAGE_INGESTING, "percent": STAGE_PERCENT[STAGE_INGESTING]})]


def test_raise_if_cancelled_raises_and_reports_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = "task-xyz"
    redis = _FakeRedis(flags={INGEST_CANCEL_KEY.format(task_id=task_id)})
    monkeypatch.setattr(job_mod, "_broker_redis", lambda: redis)
    captured: list[tuple] = []
    monkeypatch.setattr(
        job_mod,
        "report_status",
        lambda _s, doc, tenant, status, details=None: captured.append((status, details)),
    )
    with pytest.raises(IngestCancelled):
        raise_if_cancelled(object(), "doc-1", "org-1", task_id)
    assert ("CANCELLED", {"stage": "cancelled"}) in captured


def test_raise_if_cancelled_noop_when_not_cancelled(monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis) -> None:
    # No flag set → returns without raising or reporting.
    monkeypatch.setattr(job_mod, "report_status", lambda *_a, **_k: pytest.fail("should not report"))
    raise_if_cancelled(object(), "doc-1", "org-1", "task-none")
