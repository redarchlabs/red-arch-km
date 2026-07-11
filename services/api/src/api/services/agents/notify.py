"""Notification writing + delivery.

Every notification writes the durable in-app inbox row (drives the inbox + badge).
When ``settings`` is provided and SMTP is configured, it is also emailed out-of-band
to the recipient's address (or the configured ``AGENT_NOTIFY_EMAIL`` fallback), so a
bubbled escalation / pending approval reaches a human who isn't watching the console.
The notify-workflow channel (Slack/Teams/SMS via an org workflow) hangs off the same
``delivered_channels`` seam and is a follow-up.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.agent_run import AgentNotification
from api.models.user import UserProfile

logger = logging.getLogger(__name__)


async def _resolve_email(
    session: AsyncSession,
    settings: Settings,
    recipient_profile_id: uuid.UUID | None,
) -> str | None:
    if recipient_profile_id is not None:
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.id == recipient_profile_id))
        ).scalar_one_or_none()
        if profile is not None and profile.email:
            return profile.email
    return settings.agent_notify_email or None


async def _try_email(
    session: AsyncSession,
    settings: Settings,
    recipient_profile_id: uuid.UUID | None,
    title: str,
    body: str | None,
) -> bool:
    from api.services.email import EmailSender

    sender = EmailSender(settings)
    if not sender.is_configured():
        return False
    to = await _resolve_email(session, settings, recipient_profile_id)
    if not to:
        return False
    try:
        await sender.send(to=to, subject=f"[KM2 Agents] {title}", text=body or title)
        return True
    except Exception:  # noqa: BLE001 - email delivery must never fail the run
        logger.warning("agent notification email to %s failed", to)
        return False


async def create_notification(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    run_id: uuid.UUID | None = None,
    work_order_id: uuid.UUID | None = None,
    recipient_profile_id: uuid.UUID | None = None,
    recipient_role: str | None = None,
    settings: Settings | None = None,
) -> AgentNotification:
    channels = ["in_app"]
    if settings is not None and await _try_email(session, settings, recipient_profile_id, title, body):
        channels.append("email")

    notification = AgentNotification(
        kind=kind,
        title=title,
        body=body,
        run_id=run_id,
        work_order_id=work_order_id,
        recipient_profile_id=recipient_profile_id,
        recipient_role=recipient_role,
        status="unread",
        delivered_channels=channels,
        org_id=org_id,
    )
    session.add(notification)
    await session.flush()
    return notification
