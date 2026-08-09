"""Tickets — how a browser opens an authenticated WebSocket.

A browser cannot set headers on a WebSocket, and a Clerk bearer token has no
business in a URL: it lands in access logs, proxy logs and browser history, and it
carries the user's whole session. A ticket is opaque, single-use and dead in a
minute, so one found in a log is worth nothing.
"""

from __future__ import annotations

import uuid

import pytest
from api.services.agents.live import tickets

pytestmark = pytest.mark.unit


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)


ORG = uuid.uuid4()
PROFILE = uuid.uuid4()


class TestTickets:
    async def test_a_ticket_names_the_org_and_the_person(self) -> None:
        redis = _Redis()

        raw = await tickets.mint(redis, org_id=ORG, profile_id=PROFILE)
        granted = await tickets.consume(redis, raw)

        assert granted is not None
        assert granted.org_id == ORG
        assert granted.profile_id == PROFILE

    async def test_it_can_only_be_spent_once(self) -> None:
        """Two sockets racing the same ticket cannot both win — only one GETDEL
        returns a value."""
        redis = _Redis()
        raw = await tickets.mint(redis, org_id=ORG, profile_id=PROFILE)

        assert await tickets.consume(redis, raw) is not None
        assert await tickets.consume(redis, raw) is None

    async def test_it_expires_on_its_own(self) -> None:
        redis = _Redis()

        raw = await tickets.mint(redis, org_id=ORG, profile_id=PROFILE)

        assert redis.ttls[f"agent:live:ticket:{raw}"] == tickets.TICKET_TTL_SECONDS

    async def test_an_unknown_ticket_grants_nothing(self) -> None:
        assert await tickets.consume(_Redis(), "not-a-real-ticket") is None
        assert await tickets.consume(_Redis(), "") is None

    async def test_the_client_never_says_which_org_it_is_in(self) -> None:
        """The org is read from the ticket, never from the request — so a guessed
        work order id from another org resolves to a channel nobody publishes on."""
        redis = _Redis()
        raw = await tickets.mint(redis, org_id=ORG, profile_id=None)

        granted = await tickets.consume(redis, raw)

        assert granted is not None and granted.org_id == ORG
