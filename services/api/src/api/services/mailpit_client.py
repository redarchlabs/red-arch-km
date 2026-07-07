"""HTTP client for the Mailpit dev/staging mail-capture service.

Mailpit (docker service ``km2_mailpit``) captures every message the API sends
over SMTP and exposes them via a REST API on port 8025. The site-admin "Sent
Emails" console reads that API so operators can inspect sent mail without opening
Mailpit's own UI.

Mailpit is a dev/staging tool — in production the API talks to a real SMTP relay
and nothing is captured. Callers must therefore tolerate the API being
unreachable; that is signalled with ``MailpitUnavailableError`` rather than a
raw transport exception, so the console can render a friendly "not running"
state instead of a 500.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

from api.config import Settings

logger = logging.getLogger(__name__)

# Mailpit is either up on the same host/network or entirely absent — a short
# timeout keeps the console snappy and turns "not deployed" into a fast, clear
# unavailable state rather than a long hang.
_TIMEOUT_SECONDS = 5.0


class MailpitUnavailableError(RuntimeError):
    """Raised when the Mailpit API can't be reached (not running / not deployed)."""


class MailpitClient:
    """Thin async wrapper around the Mailpit message API."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.mailpit_api_url.rstrip("/")

    async def list_messages(self, *, start: int = 0, limit: int = 50) -> dict[str, Any]:
        """Return a page of captured messages (newest first, as Mailpit orders them)."""
        return await self._get("/api/v1/messages", params={"start": start, "limit": limit})

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        """Return one captured message's full headers + body, or None if it's gone.

        Mailpit is a ring buffer (MP_MAX_MESSAGES), so a message listed a moment
        ago may have been evicted — a 404 is expected and surfaced as None.
        """
        try:
            return await self._get(f"/api/v1/message/{message_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.NOT_FOUND:
                return None
            raise MailpitUnavailableError(str(exc)) from exc

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.get(f"{self._base_url}{path}", params=params)
                response.raise_for_status()
                return cast("dict[str, Any]", response.json())
        except httpx.HTTPStatusError:
            # A concrete HTTP status (e.g. 404) is meaningful to the caller — let it
            # decide. Transport failures below are flattened to "unavailable".
            raise
        except httpx.HTTPError as exc:
            logger.info("Mailpit API unreachable at %s: %s", self._base_url, exc)
            raise MailpitUnavailableError(str(exc)) from exc
