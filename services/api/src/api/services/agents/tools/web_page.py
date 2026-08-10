"""Open a public web page and report what is actually on it.

The first version of this shelled the Claude Code CLI, because that was the one
component on the deployment already able to reach the web. It worked, and then the
researcher using it hit the wall that mattered: the CLI's WebFetch converts a page to
markdown before returning it, and markdown has no ``<head>``. Title, meta description,
canonical, robots directives — the fields an SEO audit is largely *made of* — were all
invisible, and the agent came back asking whether to fill a whole CSV column with
HEAD_NOT_AVAILABLE.

Neither of the options it offered was worth taking, because the premise was wrong:
fetching a public URL does not need a model, a subscription, or a subprocess. An HTTP
GET from this process returns the raw bytes, head and all, for nothing. So this tool
does that and parses the fields itself — deterministic, free, and incapable of
describing a page it failed to load, which the model-in-the-loop version was not.

Safety is the whole of the rest of this module. A tool that fetches a URL an agent
names is a server-side request forgery primitive unless every one of these holds:

* the scheme is http(s) — no ``file://``, no ``gopher://``;
* every hostname resolves to a **global** address, checked against the resolved IP
  rather than the spelling, so ``127.0.0.1.nip.io`` and a DNS record pointing at
  169.254.169.254 are both refused;
* redirects are followed by hand, re-validating each hop, because a public URL that
  302s to the metadata endpoint defeats a check done only on the first URL;
* the response is size-capped and time-bounded.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20.0
_MAX_REDIRECTS = 4
# Enough for any real page's markup; a cap because an agent can name a URL serving a
# gigabyte and this runs inside the API process.
_MAX_BYTES = 2_000_000
# What the model is handed. The parsed fields carry the SEO signal; the excerpt is
# there for "what does this page say", and a full page would crowd out the run.
_MAX_TEXT = 6_000

_UA = "RedArchKM2/1.0 (+agent fetch_web_page; contact site owner)"

# Tags whose contents are markup, not words.
_SKIP_TEXT_IN = {"script", "style", "noscript", "template", "svg"}


class _PageParser(HTMLParser):
    """Pulls out the handful of fields a page audit actually turns on."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.h1: list[str] = []
        self.links = 0
        self.images_without_alt = 0
        self._stack: list[str] = []
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack.append(tag)
        got = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (got.get("name") or got.get("property") or "").lower()
            if name and got.get("content"):
                self.meta[name] = got["content"].strip()
        elif tag == "link" and "canonical" in got.get("rel", "").lower():
            self.canonical = got.get("href")
        elif tag == "a" and got.get("href"):
            self.links += 1
        elif tag == "img" and not got.get("alt"):
            self.images_without_alt += 1

    def handle_endtag(self, tag: str) -> None:
        while self._stack:
            if self._stack.pop() == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        current = self._stack[-1]
        text = data.strip()
        if not text or current in _SKIP_TEXT_IN:
            return
        if current == "title" and self.title is None:
            self.title = text
        elif current == "h1":
            self.h1.append(text)
        if not set(self._stack) & _SKIP_TEXT_IN:
            self._text.append(text)

    def text(self) -> str:
        return " ".join(self._text)[:_MAX_TEXT]


def _resolves_public(host: str) -> bool:
    """Does every address this host resolves to sit on the public internet?

    Checked on the resolved IP, not the spelling. A name like ``127.0.0.1.nip.io``
    looks external and is not, and an attacker-controlled DNS record can point a
    perfectly ordinary domain at the cloud metadata endpoint.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


async def _public_url(raw: str) -> str | None:
    """The URL to fetch, or None if it is not a public http(s) address."""
    try:
        parsed = urlparse((raw or "").strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    # getaddrinfo blocks; off the event loop so one slow resolver cannot stall the API.
    if not await asyncio.to_thread(_resolves_public, parsed.hostname):
        return None
    return raw.strip()


async def _fetch(url: str) -> dict[str, Any]:
    """GET ``url``, following redirects by hand so every hop is re-validated."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
        seen: list[str] = []
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            seen.append(current)
            response = await client.get(current, headers={"User-Agent": _UA})
            if response.status_code not in (301, 302, 303, 307, 308):
                body = response.content[:_MAX_BYTES]
                return {
                    "status": response.status_code,
                    "final_url": current,
                    "redirects": seen[:-1],
                    "content_type": response.headers.get("content-type", ""),
                    "bytes": len(response.content),
                    "body": body.decode(response.encoding or "utf-8", "replace"),
                }
            target = response.headers.get("location")
            if not target:
                return {"status": response.status_code, "final_url": current, "redirects": seen[:-1], "body": ""}
            nxt = await _public_url(urljoin(current, target))
            if nxt is None:
                # The classic SSRF: a public URL that redirects inward.
                return {"error": f"Refused a redirect to a non-public address from {current}."}
            current = nxt
        return {"error": f"Too many redirects (more than {_MAX_REDIRECTS}) starting at {url}."}


async def _fetch_web_page(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    url = await _public_url(str(args.get("url") or ""))
    if not url:
        return {
            "error": (
                "A public http(s) URL is required. Private, loopback, link-local and "
                "non-resolving addresses are refused."
            )
        }
    try:
        raw = await _fetch(url)
    except httpx.HTTPError as exc:
        # The page not loading is a finding, not a crash — "the homepage times out"
        # is exactly what an audit is looking for.
        return {"url": url, "error": f"Could not load the page: {type(exc).__name__}: {exc}"}
    if "error" in raw:
        return {"url": url, **raw}

    out: dict[str, Any] = {
        "url": url,
        "final_url": raw["final_url"],
        "redirects": raw["redirects"],
        "status": raw["status"],
        "content_type": raw.get("content_type", ""),
        "bytes": raw.get("bytes", 0),
    }
    if "html" not in out["content_type"] and not raw["body"].lstrip().startswith("<"):
        # robots.txt, sitemap.xml, JSON — return it as it came.
        out["text"] = raw["body"][:_MAX_TEXT]
        return out

    parser = _PageParser()
    parser.feed(raw["body"])
    out.update(
        {
            "title": parser.title,
            "meta_description": parser.meta.get("description"),
            "meta_robots": parser.meta.get("robots"),
            "og_title": parser.meta.get("og:title"),
            "canonical": parser.canonical,
            "h1": parser.h1,
            "link_count": parser.links,
            "images_without_alt": parser.images_without_alt,
            "text": parser.text(),
        }
    )
    return out


FETCH_WEB_PAGE = ToolSpec(
    name="fetch_web_page",
    description=(
        "Open a public web page and get back what is actually on it: HTTP status, the "
        "redirect chain, and — for HTML — the title, meta description, meta robots, "
        "canonical link, H1s, link count, images missing alt text, and the visible text. "
        "Non-HTML (robots.txt, sitemap.xml, JSON) comes back verbatim. This is a direct "
        "fetch, so it sees the real <head> rather than a summary, and it needs no API key. "
        "Read-only: it cannot change anything or read local files. A page that fails to "
        "load returns the error, which is itself a finding — never guess what a page says."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The full public http(s) URL to open."}},
        "required": ["url"],
    },
    # READ and non-side-effecting, like web_research and for the same reason: reading a
    # public page is not acting on the world. That is what lets an *advisory* agent hold
    # it — and an advisory researcher that cannot open a web page is not advisory, it is
    # stuck, which is precisely how this work order lost its first seven hours.
    category=Category.READ,
    handler=_fetch_web_page,
    side_effecting=False,
    always_allowed=False,
)
