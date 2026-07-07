"""Unit tests for the site-admin sent-email endpoints (/api/admin/emails).

The endpoints proxy the Mailpit container's REST API. Tests replace
``admin.MailpitClient`` with a fake so nothing touches a real Mailpit — the same
monkeypatch trick the Celery router tests use for ``BrainAPIClient``.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from api.auth.dependencies import CurrentUser, get_current_user
from api.config import Settings, get_settings
from api.routers import admin as admin_module
from api.routers.admin import router as admin_router
from api.services.mailpit_client import MailpitUnavailableError
from fastapi import FastAPI


def _user(*, is_site_admin: bool) -> CurrentUser:
    return CurrentUser(
        sub="user_x",
        username="x",
        email="x@example.com",
        profile_id=uuid.uuid4(),
        is_site_admin=is_site_admin,
    )


class _FakeMailpit:
    """Stand-in for MailpitClient: returns canned payloads or raises to simulate down."""

    def __init__(
        self,
        *,
        list_payload: dict[str, Any] | None = None,
        message_payload: dict[str, Any] | None | object = ...,
        unavailable: bool = False,
    ) -> None:
        self._list_payload = list_payload or {}
        self._message_payload = message_payload
        self._unavailable = unavailable

    async def list_messages(self, *, start: int = 0, limit: int = 50) -> dict[str, Any]:
        if self._unavailable:
            raise MailpitUnavailableError("connection refused")
        return self._list_payload

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        if self._unavailable:
            raise MailpitUnavailableError("connection refused")
        # A sentinel (...) means "not found" (404 → None from the real client).
        return None if self._message_payload is ... else self._message_payload  # type: ignore[return-value]


def _build_app(current: CurrentUser, fake: _FakeMailpit) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/admin")
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    return app


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeMailpit]:
    """Wire ``admin.MailpitClient(settings)`` to the fake the test installs."""
    holder: dict[str, _FakeMailpit] = {}
    monkeypatch.setattr(admin_module, "MailpitClient", lambda _s: holder["fake"])
    return holder


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_list_requires_site_admin(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit()
    async with _client(_build_app(_user(is_site_admin=False), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails")
    assert resp.status_code == 403


async def test_list_parses_messages(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit(
        list_payload={
            "total": 2,
            "messages": [
                {
                    "ID": "abc",
                    "From": {"Name": "KM", "Address": "no-reply@km.test"},
                    "To": [{"Name": "", "Address": "user@x.test"}],
                    "Subject": "Your form link",
                    "Created": "2026-07-07T10:00:00Z",
                    "Size": 1234,
                    "Attachments": 0,
                    "Snippet": "Please complete...",
                },
                {"no_id": True},  # skipped — no ID
            ],
        }
    )
    async with _client(_build_app(_user(is_site_admin=True), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["total"] == 2
    assert len(body["messages"]) == 1
    msg = body["messages"][0]
    assert msg["id"] == "abc"
    assert msg["from_addr"] == {"name": "KM", "address": "no-reply@km.test"}
    assert msg["to"] == [{"name": None, "address": "user@x.test"}]
    assert msg["subject"] == "Your form link"


async def test_list_reports_unavailable_instead_of_500(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit(unavailable=True)
    async with _client(_build_app(_user(is_site_admin=True), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["messages"] == []
    assert body["detail"]


async def test_list_defaults_total_to_message_count(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit(
        list_payload={"messages": [{"ID": "a", "Subject": "x"}]}  # no "total" key
    )
    async with _client(_build_app(_user(is_site_admin=True), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails")
    assert resp.json()["total"] == 1


async def test_detail_returns_body_and_attachments(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit(
        message_payload={
            "ID": "abc",
            "From": {"Name": "", "Address": "no-reply@km.test"},
            "To": [{"Name": "", "Address": "user@x.test"}],
            "Cc": [],
            "Subject": "Hi",
            "Date": "2026-07-07T10:00:00Z",
            "Text": "plain body",
            "HTML": "<p>rich body</p>",
            "Attachments": [{"FileName": "a.pdf", "PartID": "2"}, {"PartID": "3"}],
        }
    )
    async with _client(_build_app(_user(is_site_admin=True), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails/abc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "abc"
    assert body["text"] == "plain body"
    assert body["html"] == "<p>rich body</p>"
    assert body["attachments"] == ["a.pdf", "3"]


async def test_detail_404_when_message_missing(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit(message_payload=...)  # sentinel → None
    async with _client(_build_app(_user(is_site_admin=True), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails/missing")
    assert resp.status_code == 404


async def test_detail_502_when_mailpit_down(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit(unavailable=True)
    async with _client(_build_app(_user(is_site_admin=True), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails/abc")
    assert resp.status_code == 502


async def test_detail_requires_site_admin(_patch_client: dict[str, _FakeMailpit]) -> None:
    _patch_client["fake"] = _FakeMailpit(message_payload={"ID": "abc", "Subject": "x"})
    async with _client(_build_app(_user(is_site_admin=False), _patch_client["fake"])) as client:
        resp = await client.get("/api/admin/emails/abc")
    assert resp.status_code == 403
