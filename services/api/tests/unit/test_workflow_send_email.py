"""Unit tests for the send_email workflow action + template rendering."""

from __future__ import annotations

import uuid

import pytest
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError, _render_template


def _ctx(config, sent_box=None):
    async def _send(to: str, subject: str, body: str) -> bool:
        if sent_box is not None:
            sent_box.append({"to": to, "subject": subject, "body": body})
        return True

    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before={"status": "open"},
        after={"email": "jo@x.com", "name": "Jo", "status": "closed"},
        config=config,
        trigger_repo=None,  # type: ignore[arg-type]
        repo_for_slug=None,  # type: ignore[arg-type]
        send_email=None if sent_box is None else _send,
    )


class TestRenderTemplate:
    def test_substitutes_after_and_before(self) -> None:
        out = _render_template("Hi {{after.name}} ({{before.status}} -> {{after.status}})", {
            "before": {"status": "open"}, "after": {"name": "Jo", "status": "closed"}
        })
        assert out == "Hi Jo (open -> closed)"

    def test_missing_field_renders_empty(self) -> None:
        assert _render_template("x{{after.nope}}y", {"after": {}}) == "xy"

    def test_untemplated_text_unchanged(self) -> None:
        assert _render_template("plain text", {"after": {}}) == "plain text"


class TestSendEmailAction:
    @pytest.mark.asyncio
    async def test_renders_and_sends(self) -> None:
        sent: list[dict] = []
        handler = ACTION_REGISTRY["send_email"]
        ctx = _ctx(
            {"to": "{{after.email}}", "subject": "Re: {{after.name}}", "body": "Status {{after.status}}"},
            sent_box=sent,
        )
        out = await handler.execute(ctx)
        assert out["sent"] is True
        assert out["to"] == "jo@x.com"
        assert sent == [{"to": "jo@x.com", "subject": "Re: Jo", "body": "Status closed"}]

    @pytest.mark.asyncio
    async def test_missing_recipient_raises(self) -> None:
        handler = ACTION_REGISTRY["send_email"]
        ctx = _ctx({"to": "", "subject": "s", "body": "b"}, sent_box=[])
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    @pytest.mark.asyncio
    async def test_ref_recipient(self) -> None:
        sent: list[dict] = []
        handler = ACTION_REGISTRY["send_email"]
        ctx = _ctx({"to": {"$ref": "after.email"}, "subject": "s", "body": "b"}, sent_box=sent)
        out = await handler.execute(ctx)
        assert out["to"] == "jo@x.com"

    @pytest.mark.asyncio
    async def test_unavailable_email_raises(self) -> None:
        handler = ACTION_REGISTRY["send_email"]
        ctx = _ctx({"to": "a@b.com", "subject": "s", "body": "b"})  # send_email=None
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    def test_simulate_is_side_effect_free(self) -> None:
        handler = ACTION_REGISTRY["send_email"]
        ctx = _ctx({"to": "{{after.email}}", "subject": "s", "body": "b"})
        out = handler.simulate(ctx)
        assert out["to"] == "jo@x.com"
