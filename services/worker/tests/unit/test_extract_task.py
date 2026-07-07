"""Tests for task_extract_and_ingest status reporting + progress + cancellation.

Regression coverage for the empty-scan fix (blank/whitespace extraction must be
FAILED, not SUCCESS) plus the coarse progress stages and mid-flight cancellation.
Storage/extraction/brain and the Redis-backed job helpers are monkeypatched so
this runs without MinIO, tesseract, a network, or a broker.
"""

from __future__ import annotations

import pytest
import worker.tasks.extract as extract_mod
from worker.tasks._job import STAGE_EXTRACTING, STAGE_INGESTING, STAGE_QUEUED, IngestCancelled


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state: dict[str, object] = {
        "statuses": [],  # terminal (FAILED/CANCELLED) reports via report_status
        "stages": [],  # coarse progress stages via report_progress
        "brain_called": False,
    }

    class _Storage:
        def __init__(self, *_a: object, **_k: object) -> None: ...
        def get_object(self, _key: str) -> bytes:
            return b"%PDF-fake"

    def _report(_settings: object, _doc: str, _tenant: str, status: str, details: object = None) -> None:
        state["statuses"].append((status, details))  # type: ignore[union-attr]

    def _progress(_settings: object, _doc: str, _tenant: str, stage: str, **_k: object) -> None:
        state["stages"].append(stage)  # type: ignore[union-attr]

    def _post(*_a: object, **_k: object) -> dict[str, object]:
        state["brain_called"] = True
        return {"status": "success", "chunks": 1, "triplets": 0}

    monkeypatch.setattr(extract_mod, "StorageClient", _Storage)
    monkeypatch.setattr(extract_mod, "report_status", _report)
    monkeypatch.setattr(extract_mod, "report_progress", _progress)
    monkeypatch.setattr(extract_mod, "job_log", lambda *_a, **_k: None)
    # Not cancelled by default; individual tests override to exercise the abort.
    monkeypatch.setattr(extract_mod, "raise_if_cancelled", lambda *_a, **_k: None)
    monkeypatch.setattr(extract_mod, "post_to_brain_and_report", _post)
    monkeypatch.setattr(extract_mod, "_resolve_openai_key", lambda *_a, **_k: None)
    return state


def _data() -> dict[str, object]:
    return {
        "document_id": "doc-1",
        "tenant_id": "org-1",
        "document_key": "key-1",
        "document_url": "org-1/key-1/scan.pdf",
        "filename": "scan.pdf",
        "title": "Scan",
        "translation_method": "ocr",
    }


def _run(data: dict[str, object]) -> dict[str, object]:
    # bind=True task: __call__ binds self automatically.
    return extract_mod.task_extract_and_ingest(data)  # type: ignore[operator]


def test_whitespace_only_extraction_reports_failed(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_mod, "extract_text", lambda *_a, **_k: "   \n  \t")
    result = _run(_data())

    assert result["status"] == "failed"
    assert captured["brain_called"] is False
    # Progress moved through queued -> downloading -> extracting before failing.
    assert STAGE_QUEUED in captured["stages"]  # type: ignore[operator]
    assert STAGE_EXTRACTING in captured["stages"]  # type: ignore[operator]
    failed = [s for s in captured["statuses"] if s[0] == "FAILED"]  # type: ignore[union-attr]
    assert failed and failed[0][1]["reason"] == "empty_text"  # type: ignore[index]


def test_real_text_extraction_posts_to_brain(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_mod, "extract_text", lambda *_a, **_k: "real extracted text")
    result = _run(_data())

    assert result["status"] == "success"
    assert captured["brain_called"] is True
    assert not any(s[0] == "FAILED" for s in captured["statuses"])  # type: ignore[union-attr]
    # Reached the ingest stage (last coarse stage before the brain POST).
    assert STAGE_INGESTING in captured["stages"]  # type: ignore[operator]


def test_cancellation_before_extraction_aborts(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_mod, "extract_text", lambda *_a, **_k: "real text")

    # Simulate a cancel arriving: the first stage-boundary check raises.
    def _cancel(*_a: object, **_k: object) -> None:
        raise IngestCancelled("doc-1")

    monkeypatch.setattr(extract_mod, "raise_if_cancelled", _cancel)
    result = _run(_data())

    assert result["status"] == "cancelled"
    assert captured["brain_called"] is False
