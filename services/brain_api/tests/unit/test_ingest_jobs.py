"""Tests for the in-memory background-ingest job registry."""

from __future__ import annotations

from brain_api.services.ingest_jobs import IngestJobRegistry


def test_unknown_key_returns_none() -> None:
    reg = IngestJobRegistry()
    assert reg.get("t", "doc") is None
    assert reg.is_running("t", "doc") is False


def test_running_then_done_transition() -> None:
    reg = IngestJobRegistry()
    reg.mark_running("t", "doc")
    assert reg.is_running("t", "doc") is True

    reg.mark_done("t", "doc", chunks=5, triplets=3)
    job = reg.get("t", "doc")
    assert job is not None
    assert job.state == "done"
    assert job.chunks == 5
    assert job.triplets == 3
    assert reg.is_running("t", "doc") is False


def test_failed_transition_truncates_error() -> None:
    reg = IngestJobRegistry()
    reg.mark_running("t", "doc")
    reg.mark_failed("t", "doc", "x" * 1000)
    job = reg.get("t", "doc")
    assert job is not None
    assert job.state == "failed"
    assert len(job.error or "") == 500


def test_mark_progress_updates_running_job() -> None:
    reg = IngestJobRegistry()
    reg.mark_running("t", "doc")
    reg.mark_progress("t", "doc", phase="knowledge", progress=0.5)
    job = reg.get("t", "doc")
    assert job is not None
    assert job.state == "running"
    assert job.phase == "knowledge"
    assert job.progress == 0.5


def test_mark_progress_is_monotonic_and_clamped() -> None:
    reg = IngestJobRegistry()
    reg.mark_running("t", "doc")
    reg.mark_progress("t", "doc", phase="knowledge", progress=0.6)
    # A lower (out-of-order) value must not rewind the bar.
    reg.mark_progress("t", "doc", phase="knowledge", progress=0.3)
    assert reg.get("t", "doc").progress == 0.6  # type: ignore[union-attr]
    # And it's clamped to [0, 1].
    reg.mark_progress("t", "doc", phase="knowledge", progress=5.0)
    assert reg.get("t", "doc").progress == 1.0  # type: ignore[union-attr]


def test_mark_progress_noop_when_not_running() -> None:
    reg = IngestJobRegistry()
    # No job yet — silently ignored.
    reg.mark_progress("t", "doc", phase="embedding", progress=0.2)
    assert reg.get("t", "doc") is None
    # A finished job is not reopened by a late progress callback.
    reg.mark_running("t", "doc")
    reg.mark_done("t", "doc", chunks=1, triplets=0)
    reg.mark_progress("t", "doc", phase="embedding", progress=0.9)
    job = reg.get("t", "doc")
    assert job is not None
    assert job.state == "done"
    assert job.progress == 0.0


def test_keys_are_tenant_scoped() -> None:
    reg = IngestJobRegistry()
    reg.mark_running("t1", "doc")
    assert reg.is_running("t1", "doc") is True
    # Same document_key in a different tenant is a different job.
    assert reg.is_running("t2", "doc") is False
