"""Minimal SMTP email sender.

Uses the stdlib ``smtplib`` (no extra dependency) run in a worker thread so the
blocking send never stalls the event loop. Email is *disabled* unless both
``smtp_host`` and ``smtp_from`` are configured — callers check ``is_configured``
and treat a missing config as "don't send" rather than an error, so dev/test
never attempts delivery.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import smtplib
from email.message import EmailMessage

from api.config import Settings

logger = logging.getLogger(__name__)

# Pragmatic RFC-5321-ish check (no external email-validator dependency): a single
# @, non-empty local part, and a dotted domain with a 2+ char TLD. Deliberately
# strict enough to reject header-injection payloads (newlines/commas) and obvious
# garbage before we ever hand the address to smtplib.
_EMAIL_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[A-Za-z]{2,}$")


def is_valid_email(address: str) -> bool:
    """True if ``address`` is a well-formed single email address (no CR/LF)."""
    if not address or "\n" in address or "\r" in address or len(address) > 320:
        return False
    return bool(_EMAIL_RE.match(address.strip()))


class EmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return bool(self._settings.smtp_host and self._settings.smtp_from)

    async def send(self, *, to: str, subject: str, text: str, html: str | None = None) -> None:
        """Send one email. Raises on transport failure; callers decide whether
        that should fail their operation (link creation does not)."""
        if not self.is_configured():
            raise RuntimeError("SMTP is not configured")
        message = EmailMessage()
        message["From"] = self._settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        s = self._settings
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_use_tls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(message)


def render_intake_email(*, form_name: str, url: str, org_name: str | None = None) -> tuple[str, str, str]:
    """Return ``(subject, text, html)`` for an intake-form invitation.

    ``form_name``/``org_name`` are operator-controlled but still interpolated
    into an HTML body delivered to an external recipient, so they MUST be
    HTML-escaped — otherwise a form named ``<img src=x onerror=...>`` becomes
    live markup/JS in the recipient's inbox. ``url`` is minted by us (a token
    path) but is escaped too for defence in depth.
    """
    who = f"{org_name} " if org_name else ""
    subject = f"Please complete: {form_name}"
    # Plain-text part is inert (no markup interpretation) — leave unescaped.
    text = (
        f"{who}has asked you to fill out a short form: {form_name}.\n\n"
        f"Open this link to complete it:\n{url}\n\n"
        "This link is personal to you — please don't forward it."
    )
    # HTML part: escape every interpolated value. ``quote=True`` also escapes the
    # double-quote so a value can't break out of the href attribute.
    esc_form_name = html.escape(form_name)
    esc_who = f"{html.escape(org_name)} " if org_name else ""
    esc_url = html.escape(url, quote=True)
    html_body = (
        f"<div style=\"font-family:system-ui,sans-serif;max-width:480px;margin:0 auto\">"
        f"<h2 style=\"margin:0 0 12px\">{esc_form_name}</h2>"
        f"<p style=\"color:#444\">{esc_who}has asked you to fill out a short form.</p>"
        f"<p style=\"margin:24px 0\"><a href=\"{esc_url}\" "
        f"style=\"background:#b45309;color:#fff;padding:10px 18px;border-radius:6px;"
        f"text-decoration:none;display:inline-block\">Open the form</a></p>"
        f"<p style=\"color:#888;font-size:12px\">This link is personal to you — please don't forward it.</p>"
        f"</div>"
    )
    return subject, text, html_body
