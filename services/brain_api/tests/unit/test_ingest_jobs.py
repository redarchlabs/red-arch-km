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


def test_keys_are_tenant_scoped() -> None:
    reg = IngestJobRegistry()
    reg.mark_running("t1", "doc")
    assert reg.is_running("t1", "doc") is True
    # Same document_key in a different tenant is a different job.
    assert reg.is_running("t2", "doc") is False
