"""Unit tests for the send_webhook action: allow-list + SSRF guards + simulate."""

from __future__ import annotations

import uuid

import pytest
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError

pytestmark = pytest.mark.unit


def _ctx(
    config: dict, *, allowlist: tuple[str, ...] = (), trusted: tuple[str, ...] = ()
) -> ActionContext:
    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before={"a": 1},
        after={"b": 2},
        config=config,
        trigger_repo=None,  # type: ignore[arg-type]
        repo_for_slug=None,  # type: ignore[arg-type]
        webhook_allowlist=allowlist,
        trusted_local_hosts=trusted,
    )


class _StubResp:
    status_code = 200

    def json(self) -> dict:
        return {"ok": True}

    text = "{}"
    is_success = True


class _StubClient:
    """Records the last POST/request without touching the network."""

    last: dict = {}

    def __init__(self, *_a, **_k) -> None:  # noqa: ANN002
        pass

    async def __aenter__(self) -> "_StubClient":
        return self

    async def __aexit__(self, *_a) -> None:  # noqa: ANN002
        return None

    async def post(self, url: str, json: dict) -> _StubResp:  # noqa: A002
        _StubClient.last = {"url": url, "json": json}
        return _StubResp()

    async def request(self, method: str, url: str, headers: dict, json):  # noqa: A002, ANN001
        _StubClient.last = {"method": method, "url": url, "headers": headers, "json": json}
        return _StubResp()


class TestSendWebhookGuards:
    @pytest.mark.asyncio
    async def test_allowlisted_host_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        posted: dict = {}

        class _Resp:
            status_code = 200

        class _Client:
            def __init__(self, *_a, **_k) -> None:  # noqa: ANN002
                pass

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *_a) -> None:  # noqa: ANN002
                return None

            async def post(self, url: str, json: dict) -> _Resp:  # noqa: A002
                posted.update(url=url, json=json)
                return _Resp()

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        ctx = _ctx({"url": "https://hooks.example.com/x"}, allowlist=("hooks.example.com",))
        out = await handler.execute(ctx)
        assert out["status_code"] == 200
        assert posted["url"] == "https://hooks.example.com/x"
        assert posted["json"] == {"before": {"a": 1}, "after": {"b": 2}}

    @pytest.mark.asyncio
    async def test_non_allowlisted_raises(self) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        with pytest.raises(ActionError, match="not allow-listed"):
            await handler.execute(_ctx({"url": "https://evil.example.com/x"}, allowlist=("good.com",)))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://x"])
    async def test_non_http_scheme_rejected(self, url: str) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        with pytest.raises(ActionError, match="not allow-listed"):
            # Even if the host were allow-listed, the scheme check fails first.
            await handler.execute(_ctx({"url": url}, allowlist=("host",)))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "0.0.0.0"]  # noqa: S104 - test literal, not a bind
    )
    async def test_private_or_loopback_literal_ip_rejected(self, host: str) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        # Allow-list the literal IP so we exercise the private-address guard, not
        # the allow-list one.
        with pytest.raises(ActionError, match="private address"):
            await handler.execute(_ctx({"url": f"http://{host}/x"}, allowlist=(host,)))

    def test_simulate_performs_no_http(self) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        out = handler.simulate(_ctx({"url": "https://anything/x", "body": {"k": "v"}}))
        assert out["would_post"] == "https://anything/x"
        assert out["body"]["k"] == "v"

    @pytest.mark.asyncio
    async def test_missing_url_raises(self) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        with pytest.raises(ActionError, match="requires url"):
            await handler.execute(_ctx({}, allowlist=("host",)))


class TestTrustedLocalHosts:
    """WORKFLOW_TRUSTED_LOCAL_HOSTS lets an action reach a bridge on localhost/LAN
    (e.g. a robot-control server) while every other private host stays blocked."""

    @pytest.mark.asyncio
    async def test_trusted_loopback_ip_bypasses_private_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
        handler = ACTION_REGISTRY["send_webhook"]
        # 127.0.0.1 is normally rejected as a private address; trusting it allows
        # the call. Note it need NOT be in the allow-list — trust covers both gates.
        ctx = _ctx({"url": "http://127.0.0.1:8080/head"}, allowlist=(), trusted=("127.0.0.1",))
        out = await handler.execute(ctx)
        assert out["status_code"] == 200
        assert _StubClient.last["url"] == "http://127.0.0.1:8080/head"

    @pytest.mark.asyncio
    async def test_trusted_hostname_bypasses_allowlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
        handler = ACTION_REGISTRY["send_webhook"]
        ctx = _ctx({"url": "http://robot.local/x"}, allowlist=(), trusted=("robot.local",))
        out = await handler.execute(ctx)
        assert out["status_code"] == 200

    @pytest.mark.asyncio
    async def test_untrusted_loopback_still_rejected(self) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        # Same host, but NOT trusted (empty trusted set) — the private-address guard
        # must still fire even though it is allow-listed.
        with pytest.raises(ActionError, match="private address"):
            await handler.execute(_ctx({"url": "http://127.0.0.1/x"}, allowlist=("127.0.0.1",), trusted=()))

    @pytest.mark.asyncio
    async def test_a_different_private_host_is_not_bypassed(self) -> None:
        handler = ACTION_REGISTRY["send_webhook"]
        # Trusting 127.0.0.1 must not open up a different private host (10.0.0.5).
        with pytest.raises(ActionError, match="private address"):
            await handler.execute(
                _ctx({"url": "http://10.0.0.5/x"}, allowlist=("10.0.0.5",), trusted=("127.0.0.1",))
            )

    @pytest.mark.asyncio
    async def test_http_request_action_honors_trusted_hosts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
        handler = ACTION_REGISTRY["http_request"]
        ctx = _ctx(
            {"url": "http://127.0.0.1:8080/gesture", "method": "POST", "body": {"name": "duck"}},
            allowlist=(),
            trusted=("127.0.0.1",),
        )
        out = await handler.execute(ctx)
        assert out["ok"] is True
        assert _StubClient.last["method"] == "POST"
        assert _StubClient.last["url"] == "http://127.0.0.1:8080/gesture"
