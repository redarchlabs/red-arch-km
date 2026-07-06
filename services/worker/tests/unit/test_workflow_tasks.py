"""Unit tests for the workflow-engine worker tasks.

These tasks are thin ``httpx`` callbacks into the API's internal router. We
monkeypatch ``httpx.post`` / the settings so they run without a live API.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import worker.tasks.workflow as wf


class _Resp:
    def __init__(self, payload: dict[str, Any] | None, *, content: bytes = b"{}") -> None:
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:  # noqa: D401 - mimic httpx.Response
        return None

    def json(self) -> dict[str, Any] | None:
        return self._payload


def _settings(monkeypatch: pytest.MonkeyPatch, *, key: str) -> None:
    class _S:
        internal_api_key = key
        api_url = "http://api.local"

    monkeypatch.setattr(wf, "WorkerSettings", _S)


def test_post_noops_when_internal_api_key_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, key="")
    called = {"n": 0}

    def _post(*_a: Any, **_k: Any) -> _Resp:
        called["n"] += 1
        return _Resp({})

    monkeypatch.setattr(wf.httpx, "post", _post)
    # No key → skip the callback entirely (returns None, never touches httpx).
    assert wf._post("/api/internal/workflows/dispatch-batch") is None
    assert called["n"] == 0


def test_post_swallows_httpx_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, key="secret")

    def _boom(*_a: Any, **_k: Any) -> _Resp:
        raise httpx.ConnectError("api down")

    monkeypatch.setattr(wf.httpx, "post", _boom)
    # A transport failure must not raise (beat tasks must not crash the worker).
    assert wf._post("/api/internal/workflows/dispatch-batch") is None


def test_post_sends_key_and_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, key="secret")
    captured: dict[str, Any] = {}

    def _post(url: str, *, params: Any, headers: Any, timeout: Any) -> _Resp:
        captured.update(url=url, params=params, headers=headers)
        return _Resp({"events": 3})

    monkeypatch.setattr(wf.httpx, "post", _post)
    out = wf._post("/api/internal/workflows/dispatch-batch", {"limit": 50})
    assert out == {"events": 3}
    assert captured["url"] == "http://api.local/api/internal/workflows/dispatch-batch"
    assert captured["params"] == {"limit": 50}
    assert captured["headers"]["X-Internal-API-Key"] == "secret"


@pytest.mark.parametrize(
    ("task", "expected_path", "expected_params"),
    [
        (lambda: wf.sweep_outbox(limit=200), "/api/internal/workflows/dispatch-batch", {"limit": 200}),
        (lambda: wf.run_timers(), "/api/internal/workflows/run-timers", None),
        (
            lambda: wf.maintain_partitions(months_ahead=2),
            "/api/internal/workflows/maintain-partitions",
            {"months_ahead": 2},
        ),
    ],
)
def test_tasks_call_correct_internal_path(
    monkeypatch: pytest.MonkeyPatch, task: Any, expected_path: str, expected_params: Any
) -> None:
    seen: dict[str, Any] = {}

    def _fake_post(path: str, params: Any = None) -> dict[str, Any] | None:
        seen["path"] = path
        seen["params"] = params
        return {"ok": True}

    monkeypatch.setattr(wf, "_post", _fake_post)
    task()
    assert seen["path"] == expected_path
    assert seen["params"] == expected_params
