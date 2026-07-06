"""Unit tests for intake-email rendering (HTML escaping) + email validation."""

from __future__ import annotations

import uuid

import pytest
from api.schemas.form import GenerateLinkRequest
from api.services.email import is_valid_email, render_intake_email
from pydantic import ValidationError

pytestmark = pytest.mark.unit


class TestRenderIntakeEmail:
    def test_escapes_html_in_form_name(self) -> None:
        subject, text, html = render_intake_email(
            form_name='<img src=x onerror=alert(1)>',
            url="https://app.example/intake/tok",
        )
        # The raw payload must NOT appear as live markup in the HTML body.
        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
        # Subject is a header, not HTML — carries the literal form name.
        assert "onerror" in subject

    def test_escapes_org_name(self) -> None:
        _s, _t, html = render_intake_email(
            form_name="Onboarding",
            url="https://app.example/intake/tok",
            org_name='<script>evil()</script>',
        )
        assert "<script>evil()</script>" not in html
        assert "&lt;script&gt;" in html

    def test_escapes_url_quote_to_prevent_attr_breakout(self) -> None:
        _s, _t, html = render_intake_email(
            form_name="Form",
            url='https://x/"><script>alert(1)</script>',
        )
        assert '"><script>' not in html
        assert "&quot;&gt;&lt;script&gt;" in html


class TestIsValidEmail:
    @pytest.mark.parametrize("addr", ["jo@x.com", "a.b+tag@sub.domain.co", "x@y.io"])
    def test_valid(self, addr: str) -> None:
        assert is_valid_email(addr) is True

    @pytest.mark.parametrize(
        "addr",
        ["", "nope", "a@b", "a@@b.com", "a b@x.com", "a@x.com\nBcc: evil@x.com", "a@x.com,b@x.com"],
    )
    def test_invalid(self, addr: str) -> None:
        assert is_valid_email(addr) is False


class TestGenerateLinkRequestValidation:
    def test_rejects_malformed_recipient(self) -> None:
        with pytest.raises(ValidationError):
            GenerateLinkRequest(target_record_id=uuid.uuid4(), recipient_email="not-an-email")

    def test_rejects_header_injection(self) -> None:
        with pytest.raises(ValidationError):
            GenerateLinkRequest(
                target_record_id=uuid.uuid4(),
                recipient_email="ok@x.com\nBcc: victim@x.com",
            )

    def test_accepts_valid_and_none(self) -> None:
        assert GenerateLinkRequest(target_record_id=uuid.uuid4()).recipient_email is None
        req = GenerateLinkRequest(target_record_id=uuid.uuid4(), recipient_email=" jo@x.com ")
        assert req.recipient_email == "jo@x.com"  # trimmed
