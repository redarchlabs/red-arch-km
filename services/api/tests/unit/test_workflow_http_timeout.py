"""An http_request step can widen its own outbound timeout.

The default 10s suits an ordinary webhook, but some connectors do real work
before they answer: the robot bridge renders every line of a ``/perform``
timeline to speech server-side, so a two-minute presentation takes ~12s to
acknowledge and the step times out with an empty-message ReadTimeout — the
performance plays while KM2 records the run as failed.

``config.timeout_seconds`` lets that one step wait longer, without lifting the
default for every other outbound call in the org.
"""

from __future__ import annotations

import uuid

import pytest
from api.services.workflow import actions as A
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext

pytestmark = pytest.mark.unit


class _StubResp:
    status_code = 200
    text = "{}"
    is_success = True

    def json(self) -> dict:
        return {"ok": True}


class _StubClient:
    """Records the timeout it was constructed with, without touching the network."""

    last_timeout: object = None

    def __init__(self, *_a, timeout=None, **_k) -> None:  # noqa: ANN001, ANN002
        _StubClient.last_timeout = timeout

    async def __aenter__(self) -> _StubClient:
        return self

    async def __aexit__(self, *_a) -> None:  # noqa: ANN002
        return None

    async def request(self, method: str, url: str, headers: dict, json):  # noqa: A002, ANN001
        return _StubResp()


def _ctx(config: dict) -> ActionContext:
    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before=None,
        after=None,
        config=config,
        trigger_repo=None,  # type: ignore[arg-type]
        repo_for_slug=None,  # type: ignore[arg-type]
        trusted_local_hosts=("robot.local",),
    )


@pytest.fixture(autouse=True)
def _stub_httpx(monkeypatch: pytest.MonkeyPatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
    _StubClient.last_timeout = None


BASE = {"url": "https://robot.local/perform", "method": "POST", "body": {"script": "hi"}}


async def _run(config: dict) -> dict:
    return await ACTION_REGISTRY["http_request"].execute(_ctx(config))


class TestHttpRequestTimeout:
    async def test_defaults_to_the_shared_webhook_timeout(self) -> None:
        await _run(dict(BASE))
        assert _StubClient.last_timeout == A.WEBHOOK_TIMEOUT_SECONDS

    async def test_config_can_widen_the_timeout(self) -> None:
        await _run(dict(BASE, timeout_seconds=120))
        assert _StubClient.last_timeout == 120.0

    async def test_a_string_value_is_coerced(self) -> None:
        """View/designer JSON round-trips numbers as strings often enough to matter."""
        await _run(dict(BASE, timeout_seconds="45"))
        assert _StubClient.last_timeout == 45.0

    @pytest.mark.parametrize("bad", [0, -5, "abc", None, {"a": 1}])
    async def test_unusable_values_fall_back_to_the_default(self, bad: object) -> None:
        await _run(dict(BASE, timeout_seconds=bad))
        assert _StubClient.last_timeout == A.WEBHOOK_TIMEOUT_SECONDS

    async def test_timeout_is_capped(self) -> None:
        """A step must not be able to pin a worker slot indefinitely."""
        await _run(dict(BASE, timeout_seconds=999_999))
        assert _StubClient.last_timeout == A.MAX_OUTBOUND_TIMEOUT_SECONDS
