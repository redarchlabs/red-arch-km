"""Opening a public page: what comes back, and what is refused.

Two failures are pinned here. The first is why the tool exists at all — an advisory
researcher could not open a web page, because the only tool that could reach the web
was EXECUTE and the kind-gate bars advisory agents from EXECUTE before grants are read.
The second is why it fetches directly rather than through the Claude CLI: WebFetch
converts a page to markdown, markdown has no ``<head>``, and the researcher came back
asking whether to fill an entire SEO CSV with HEAD_NOT_AVAILABLE.

The rest is SSRF. A tool that fetches a URL an agent names is a way to read the host it
runs on unless every hop is checked against a resolved address.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from api.models.agent import Agent
from api.services.agents.authority import Decision, decide
from api.services.agents.kind_gate import kind_gate
from api.services.agents.tools.registry import base_tool_specs
from api.services.agents.tools.spec import Category, ToolContext
from api.services.agents.tools.web_page import FETCH_WEB_PAGE, _fetch_web_page, _PageParser

pytestmark = pytest.mark.unit

_PAGE = """
<html><head>
  <title>Red Arch Knowledge Manager</title>
  <meta name="description" content="Knowledge management for teams.">
  <meta name="robots" content="index,follow">
  <meta property="og:title" content="Red Arch">
  <link rel="canonical" href="https://redarchlabs.com/">
  <style>.a{color:red}</style>
</head><body>
  <h1>Red Arch Knowledge Manager</h1>
  <p>Ship knowledge, not documents.</p>
  <a href="/pricing">Pricing</a><a href="/docs">Docs</a>
  <img src="hero.png"><img src="logo.png" alt="logo">
  <script>var x = "should not appear";</script>
</body></html>
"""


def _ctx(kind: str = "advisory") -> ToolContext:
    return ToolContext(
        session=None,
        org_id=uuid.uuid4(),
        settings=SimpleNamespace(),
        agent=Agent(name="research-analyst", provider="openai", model="m", kind=kind),
    )


def _response(body: str = _PAGE, status: int = 200, ctype: str = "text/html; charset=utf-8", headers=None):
    return httpx.Response(
        status,
        content=body.encode(),
        headers={"content-type": ctype, **(headers or {})},
        request=httpx.Request("GET", "https://x"),
    )


def _public(*_a, **_k):
    """getaddrinfo stand-in resolving everything to a public address."""
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


def _private(*_a, **_k):
    return [(2, 1, 6, "", ("127.0.0.1", 0))]


class TestWhatItReturns:
    async def test_it_reads_the_head_the_cli_could_not(self, monkeypatch) -> None:
        # The whole reason for fetching directly: title, description, canonical and
        # robots are the fields an SEO audit turns on, and markdown drops all of them.
        monkeypatch.setattr("socket.getaddrinfo", _public)
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=_response())):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/"})

        assert out["status"] == 200
        assert out["title"] == "Red Arch Knowledge Manager"
        assert out["meta_description"] == "Knowledge management for teams."
        assert out["meta_robots"] == "index,follow"
        assert out["canonical"] == "https://redarchlabs.com/"
        assert out["h1"] == ["Red Arch Knowledge Manager"]

    async def test_it_counts_the_things_an_audit_asks_about(self, monkeypatch) -> None:
        monkeypatch.setattr("socket.getaddrinfo", _public)
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=_response())):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/"})

        assert out["link_count"] == 2
        assert out["images_without_alt"] == 1

    async def test_script_and_style_never_reach_the_model(self, monkeypatch) -> None:
        monkeypatch.setattr("socket.getaddrinfo", _public)
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=_response())):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/"})

        assert "Ship knowledge" in out["text"]
        assert "should not appear" not in out["text"]
        assert "color:red" not in out["text"]

    async def test_non_html_comes_back_verbatim(self, monkeypatch) -> None:
        # robots.txt and sitemap.xml are read literally or not at all.
        monkeypatch.setattr("socket.getaddrinfo", _public)
        body = "User-agent: *\nDisallow: /admin\nSitemap: https://x/sitemap.xml"
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=_response(body, ctype="text/plain"))):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/robots.txt"})

        assert out["text"] == body
        assert "title" not in out

    async def test_a_404_is_a_finding_not_an_error(self, monkeypatch) -> None:
        """redarchlabs.com serves no robots.txt. That is the audit's first result, and
        it has to arrive as data rather than as a failure the agent routes around."""
        monkeypatch.setattr("socket.getaddrinfo", _public)
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=_response("Not Found", 404, "text/plain"))):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/robots.txt"})

        assert out["status"] == 404
        assert "error" not in out

    async def test_a_page_that_will_not_load_says_so(self, monkeypatch) -> None:
        monkeypatch.setattr("socket.getaddrinfo", _public)
        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/"})

        assert "Could not load the page" in out["error"]


class TestRedirects:
    async def test_it_reports_where_it_ended_up(self, monkeypatch) -> None:
        monkeypatch.setattr("socket.getaddrinfo", _public)
        hops = [
            _response("", 301, "text/html", {"location": "https://www.redarchlabs.com/"}),
            _response(),
        ]
        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=hops)):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/"})

        assert out["final_url"] == "https://www.redarchlabs.com/"
        assert out["redirects"] == ["https://redarchlabs.com/"]

    async def test_a_redirect_inward_is_refused(self, monkeypatch) -> None:
        """The classic SSRF: a public URL that 302s to the metadata endpoint. Checking
        only the URL the agent typed would walk straight into it."""
        calls = {"n": 0}

        def _resolve(host, *_a, **_k):
            calls["n"] += 1
            return _private() if "169.254" in host or host == "metadata" else _public()

        monkeypatch.setattr("socket.getaddrinfo", _resolve)
        hops = [_response("", 302, "text/html", {"location": "http://169.254.169.254/latest/meta-data/"})]
        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=hops)):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/"})

        assert "Refused a redirect to a non-public address" in out["error"]

    async def test_a_redirect_loop_terminates(self, monkeypatch) -> None:
        monkeypatch.setattr("socket.getaddrinfo", _public)
        loop = _response("", 302, "text/html", {"location": "https://redarchlabs.com/"})
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=loop)):
            out = await _fetch_web_page(_ctx(), {"url": "https://redarchlabs.com/"})

        assert "Too many redirects" in out["error"]


class TestItRefusesTheHostItRunsOn:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/api/internal/agents/advance-runs",
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://[::1]/",
        ],
    )
    async def test_private_addresses_are_refused(self, monkeypatch, url: str) -> None:
        monkeypatch.setattr("socket.getaddrinfo", _private)
        with patch("httpx.AsyncClient.get", AsyncMock()) as m:
            out = await _fetch_web_page(_ctx(), {"url": url})

        assert "public http(s) URL is required" in out["error"]
        m.assert_not_called()

    async def test_a_public_name_pointing_inward_is_refused(self, monkeypatch) -> None:
        """127.0.0.1.nip.io spells like the internet and resolves to loopback. The
        check has to be on the resolved address, not the hostname."""
        monkeypatch.setattr("socket.getaddrinfo", _private)
        with patch("httpx.AsyncClient.get", AsyncMock()) as m:
            out = await _fetch_web_page(_ctx(), {"url": "http://127.0.0.1.nip.io/"})

        assert "error" in out
        m.assert_not_called()

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "not a url", ""])
    async def test_only_http_is_fetched(self, monkeypatch, url: str) -> None:
        monkeypatch.setattr("socket.getaddrinfo", _public)
        with patch("httpx.AsyncClient.get", AsyncMock()) as m:
            out = await _fetch_web_page(_ctx(), {"url": url})

        assert "error" in out
        m.assert_not_called()

    async def test_a_name_that_does_not_resolve_is_refused(self, monkeypatch) -> None:
        def _boom(*_a, **_k):
            raise OSError("nope")

        monkeypatch.setattr("socket.getaddrinfo", _boom)
        out = await _fetch_web_page(_ctx(), {"url": "https://no-such-host.example/"})

        assert "error" in out


class TestWhoMayUseIt:
    def test_it_is_read_only_so_an_adviser_may_hold_it(self) -> None:
        assert FETCH_WEB_PAGE.category == Category.READ
        assert FETCH_WEB_PAGE.side_effecting is False
        assert kind_gate("advisory", FETCH_WEB_PAGE) is None
        assert kind_gate("coordinator", FETCH_WEB_PAGE) is None

    def test_a_granted_advisory_researcher_is_offered_it(self) -> None:
        analyst = Agent(
            name="research-analyst",
            provider="openai",
            model="m",
            kind="advisory",
            grants={"tools": ["fetch_web_page"]},
        )
        assert decide(analyst, FETCH_WEB_PAGE).decision is not Decision.DENY

    def test_it_is_still_grant_gated(self) -> None:
        bare = Agent(name="a", provider="openai", model="m", kind="advisory", grants={"tools": []})
        assert decide(bare, FETCH_WEB_PAGE).decision is Decision.DENY

    def test_it_needs_no_cli_and_no_key(self) -> None:
        # The CLI-backed version only existed where the subscription did. This one is
        # an HTTP GET, so every deployment has it.
        names = {s.name for s in base_tool_specs(SimpleNamespace(enable_claude_cli_tool=False))}
        assert "fetch_web_page" in names


def test_the_parser_survives_a_page_with_no_head() -> None:
    parser = _PageParser()
    parser.feed("<html><body><p>bare</p></body></html>")

    assert parser.title is None and parser.canonical is None
    assert parser.text() == "bare"
