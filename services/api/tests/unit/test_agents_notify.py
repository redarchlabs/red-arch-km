"""Unit tests for notification writing + email-delivery gating."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from api.config import Settings
from api.services.agents import notify

pytestmark = pytest.mark.unit


class _Result:
    def scalar_one_or_none(self):
        return None  # no Org / no UserProfile row


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def execute(self, *args, **kwargs):
        return _Result()


def _settings(**over) -> Settings:
    base = dict(secret_key=SecretStr("x"))
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_in_app_only_when_no_settings():
    session = _FakeSession()
    n = await notify.create_notification(session, uuid4(), kind="approval", title="Need approval")
    assert n.delivered_channels == ["in_app"]
    assert session.added == [n]


@pytest.mark.asyncio
async def test_no_email_when_smtp_unconfigured():
    session = _FakeSession()
    # SMTP not configured -> EmailSender.is_configured() is False -> in_app only.
    n = await notify.create_notification(
        session, uuid4(), kind="escalation", title="Blocked", settings=_settings()
    )
    assert n.delivered_channels == ["in_app"]


@pytest.mark.asyncio
async def test_emails_when_configured(monkeypatch):
    sent = {}

    class _Sender:
        def __init__(self, settings):
            pass

        def is_configured(self):
            return True

        async def send(self, *, to, subject, text, html=None):
            sent["to"] = to
            sent["subject"] = subject

    import api.services.email as email_mod

    monkeypatch.setattr(email_mod, "EmailSender", _Sender)
    session = _FakeSession()
    n = await notify.create_notification(
        session, uuid4(), kind="approval", title="Approve me",
        settings=_settings(smtp_host="smtp.local", smtp_from="a@b.c", agent_notify_email="ops@b.c"),
    )
    assert "email" in n.delivered_channels
    assert sent["to"] == "ops@b.c"
