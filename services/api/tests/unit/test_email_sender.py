"""Unit tests for the intake email sender + template."""

from __future__ import annotations

import pytest
from api.config import Settings
from api.services.email import EmailSender, render_intake_email


def _settings(**over: object) -> Settings:
    base = {"secret_key": "x"}
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_not_configured_without_host_or_from() -> None:
    assert EmailSender(_settings()).is_configured() is False
    assert EmailSender(_settings(smtp_host="smtp.example.com")).is_configured() is False
    assert EmailSender(_settings(smtp_from="no-reply@example.com")).is_configured() is False


def test_configured_with_host_and_from() -> None:
    sender = EmailSender(_settings(smtp_host="smtp.example.com", smtp_from="no-reply@example.com"))
    assert sender.is_configured() is True


async def test_send_raises_when_not_configured() -> None:
    with pytest.raises(RuntimeError):
        await EmailSender(_settings()).send(to="a@b.com", subject="s", text="t")


def test_render_intake_email_includes_url_and_name() -> None:
    subject, text, html = render_intake_email(form_name="Patient Intake", url="http://x/intake/tok")
    assert "Patient Intake" in subject
    assert "http://x/intake/tok" in text
    assert "http://x/intake/tok" in html
