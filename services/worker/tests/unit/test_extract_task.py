"""Tests for task_extract_and_ingest status reporting.

Regression coverage for the fix where a blank/illegible scan (empty or
whitespace-only extracted text) must be reported FAILED rather than sneaking
through brain-api's min_length=1 check and being reported SUCCESS. The real
storage/extraction/brain calls are monkeypatched so this runs without MinIO,
the tesseract binary, or a network.
"""

from __future__ import annotations

import pytest
import worker.tasks.extract as extract_mod


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state: dict[str, object] = {"statuses": [], "brain_called": False}

    class _Storage:
        def __init__(self, *_a: object, **_k: object) -> None: ...
        def get_object(self, _key: str) -> bytes:
            return b"%PDF-fake"

    def _report(_settings: object, _doc: str, _tenant: str, status: str, details: object = None) -> None:
        state["statuses"].append((status, details))  # type: ignore[union-attr]

    def _post(*_a: object, **_k: object) -> dict[str, object]:
        state["brain_called"] = True
        return {"status": "success", "chunks": 1, "triplets": 0}

    monkeypatch.setattr(extract_mod, "StorageClient", _Storage)
    monkeypatch.setattr(extract_mod, "report_status", _report)
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
    statuses = captured["statuses"]
    assert ("PROCESSING", None) in statuses
    failed = [s for s in statuses if s[0] == "FAILED"]
    assert failed and failed[0][1]["reason"] == "empty_text"  # type: ignore[index]


def test_real_text_extraction_posts_to_brain(
    captured: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_mod, "extract_text", lambda *_a, **_k: "real extracted text")
    result = _run(_data())

    assert result["status"] == "success"
    assert captured["brain_called"] is True
    assert not any(s[0] == "FAILED" for s in captured["statuses"])
