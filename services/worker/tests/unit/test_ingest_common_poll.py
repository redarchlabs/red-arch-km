"""Tests for the async submit + poll logic in ``post_to_brain_and_report``.

httpx and the Redis-backed job helpers are monkeypatched, so these run without a
network or broker. They verify the worker submits to brain-api's 202 endpoint,
polls ``/ingest-status`` to completion, and reports the right terminal status.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import worker.tasks._ingest_common as ic
import worker.tasks._job as job_mod
from worker.config import WorkerSettings
from worker.tasks._job import IngestCancelled


class _Resp:
    """Minimal stand-in for httpx.Response used by post/get fakes."""

    def __init__(self, *, json_data: dict[str, Any] | None = None, status_code: int = 200, text: str = "") -> None:
        self._json = json_data or {}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("POST", "http://brain"), response=self)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._json


def _settings() -> WorkerSettings:
    return WorkerSettings(
        brain_api_url="http://brain",
        brain_api_key="k",
        brain_ingest_poll_interval_seconds=0,
        brain_ingest_accept_timeout_seconds=5,
        brain_ingest_max_wait_seconds=5400,
    )


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"statuses": [], "posts": 0}

    def _report(_s: object, _d: str, _t: str, status: str, details: object = None) -> None:
        state["statuses"].append((status, details))

    monkeypatch.setattr(ic, "report_status", _report)
    monkeypatch.setattr(job_mod, "job_log", lambda *_a, **_k: None)
    monkeypatch.setattr(job_mod, "raise_if_cancelled", lambda *_a, **_k: None)
    return state


def _run(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"max_retries": 1, "retry_delay": 0}
    kwargs.update(overrides)
    return ic.post_to_brain_and_report(
        _settings(),
        {"tenant_id": "t1", "document_key": "key-1", "text": "x"},
        document_id="doc-1",
        document_key="key-1",
        tenant_id="t1",
        **kwargs,
    )


def _terminal_status(captured: dict[str, Any]) -> str:
    return captured["statuses"][-1][0]


def test_submit_then_poll_to_done_reports_success(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: _Resp(json_data={"status": "accepted"}, status_code=202))
    states = iter(
        [
            _Resp(json_data={"state": "running"}),
            _Resp(json_data={"state": "done", "chunks": 9, "triplets": 4}),
        ]
    )
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: next(states))

    result = _run()
    assert result["status"] == "success"
    assert result["chunks"] == 9
    assert result["triplets"] == 4
    assert _terminal_status(captured) == "SUCCESS"


def test_running_polls_relay_progress_into_percent_band(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A running job reporting brain-api pipeline progress should emit PROCESSING
    # updates whose percent climbs within the 60→97 band, then SUCCESS at the end.
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: _Resp(json_data={"status": "accepted"}, status_code=202))
    states = iter(
        [
            _Resp(json_data={"state": "running", "phase": "embedding", "progress": 0.1}),
            _Resp(json_data={"state": "running", "phase": "knowledge", "progress": 0.5}),
            _Resp(json_data={"state": "running", "phase": "knowledge", "progress": 1.0}),
            _Resp(json_data={"state": "done", "chunks": 3, "triplets": 2}),
        ]
    )
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: next(states))

    result = _run()
    assert result["status"] == "success"

    processing = [d for s, d in captured["statuses"] if s == "PROCESSING"]
    percents = [d["percent"] for d in processing]
    # 60 + round(f*37): 0.1→64, 0.5→78 (banker's rounding of 18.5), 1.0→97
    assert percents == [64, 78, 97]
    assert processing[0]["stage"] == "embedding"
    assert processing[-1]["stage"] == "knowledge"
    assert _terminal_status(captured) == "SUCCESS"


def test_running_progress_does_not_report_when_unchanged(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two identical running polls must not produce duplicate PROCESSING updates.
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: _Resp(json_data={"status": "accepted"}, status_code=202))
    states = iter(
        [
            _Resp(json_data={"state": "running", "phase": "knowledge", "progress": 0.5}),
            _Resp(json_data={"state": "running", "phase": "knowledge", "progress": 0.5}),
            _Resp(json_data={"state": "done", "chunks": 1, "triplets": 0}),
        ]
    )
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: next(states))

    _run()
    processing = [d for s, d in captured["statuses"] if s == "PROCESSING"]
    assert len(processing) == 1  # second identical poll is suppressed


def test_poll_failed_reports_failed(captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: _Resp(json_data={"status": "accepted"}, status_code=202))
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: _Resp(json_data={"state": "failed", "error": "kaboom"}))

    result = _run()
    assert result["status"] == "failed"
    assert _terminal_status(captured) == "FAILED"


def test_submit_timeout_is_not_retried(captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def _timeout(*_a: Any, **_k: Any) -> _Resp:
        raise httpx.ReadTimeout("timed out")

    posts = {"n": 0}

    def _post(*a: Any, **k: Any) -> _Resp:
        posts["n"] += 1
        return _timeout()

    monkeypatch.setattr(ic.httpx, "post", _post)
    result = _run()
    assert result["status"] == "failed"
    # A read timeout must NOT be re-POSTed (that would double-ingest).
    assert posts["n"] == 1
    assert _terminal_status(captured) == "FAILED"


def test_connect_error_is_retried_then_succeeds(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def _post(*_a: Any, **_k: Any) -> _Resp:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return _Resp(json_data={"status": "accepted"}, status_code=202)

    monkeypatch.setattr(ic.httpx, "post", _post)
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: _Resp(json_data={"state": "done", "chunks": 1, "triplets": 0}))

    result = _run(max_retries=2, retry_delay=0)
    assert result["status"] == "success"
    assert calls["n"] == 2  # first ConnectError retried, second accepted


def test_cancel_during_poll_returns_cancelled(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: _Resp(json_data={"status": "accepted"}, status_code=202))
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: _Resp(json_data={"state": "running"}))

    def _cancel(*_a: Any, **_k: Any) -> None:
        raise IngestCancelled

    monkeypatch.setattr(job_mod, "raise_if_cancelled", _cancel)
    result = _run()
    assert result["status"] == "cancelled"


def test_persistent_unknown_reports_failed(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: _Resp(json_data={"status": "accepted"}, status_code=202))
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: _Resp(json_data={"state": "unknown"}))

    result = _run()
    assert result["status"] == "failed"
    assert result["error"] == "job_lost"
    assert _terminal_status(captured) == "FAILED"


def test_max_wait_exceeded_reports_failed(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: _Resp(json_data={"status": "accepted"}, status_code=202))
    monkeypatch.setattr(ic.httpx, "get", lambda *a, **k: _Resp(json_data={"state": "running"}))

    settings = _settings()
    settings.brain_ingest_max_wait_seconds = 0  # deadline is immediately in the past
    result = ic.post_to_brain_and_report(
        settings,
        {"tenant_id": "t1", "document_key": "key-1", "text": "x"},
        document_id="doc-1",
        document_key="key-1",
        tenant_id="t1",
    )
    assert result["status"] == "failed"
    assert result["error"] == "max_wait_exceeded"
