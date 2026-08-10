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

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.agent_run import AgentNotification
from api.models.org import Org
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


async def _try_notify_workflow(
    session: AsyncSession,
    org_id: uuid.UUID,
    settings: Settings,
    kind: str,
    title: str,
    body: str | None,
) -> bool:
    """Fire the org's configured notify workflow (Slack/Teams/SMS fan-out).

    Runs inside a SAVEPOINT so a workflow error rolls back only its own writes,
    never the caller's run transaction."""
    org = (await session.execute(select(Org).where(Org.id == org_id))).scalar_one_or_none()
    if org is None or org.agent_notify_workflow_id is None:
        return False
    from api.repositories.workflow import WorkflowRepository
    from api.schemas.workflow import ManualRunRequest
    from api.services.workflow.manual_run import execute_workflow_run, resolve_published_version

    wf = await WorkflowRepository(session, org_id).get(org.agent_notify_workflow_id)
    if wf is None:
        return False
    try:
        async with session.begin_nested():
            version = await resolve_published_version(session, org_id, wf)
            await execute_workflow_run(
                session,
                org_id,
                wf,
                version,
                # `operation` describes an entity-bound run; a manual workflow ignores
                # the record fields entirely, so it keeps its default. Passing
                # "manual" here raised a Pydantic ValidationError — the field is
                # Literal["create", "update", "delete"] — which broke every
                # notification that drives a workflow.
                request=ManualRunRequest(
                    record_id=None,
                    before=None,
                    after=None,
                    inputs={"kind": kind, "title": title, "body": body or ""},
                ),
                actor_user_id=None,
                settings=settings,
            )
        return True
    except Exception:  # noqa: BLE001 - notify fan-out must never fail the run
        logger.warning("agent notify-workflow failed for org %s", org_id)
        return False


async def settle_notifications(
    session: AsyncSession,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    kind: str,
) -> int:
    """Resolve the ``kind`` notifications for ``run_id``. Returns how many were closed.

    An approval or question notification exists to say "go and do this". Once the
    approval is decided or the question answered, it is a receipt — but it stayed
    ``unread``, so the inbox kept asking a person to tick off work they had already
    done. Observed live: eleven open rows, of which eight were for items settled hours
    earlier. A list of chores that are mostly already finished trains people to stop
    reading it, which is the same failure the badge exists to prevent.

    Callers settle one item and then call this only when nothing of that kind is left
    pending on the run — a run can raise a second approval while the first is being
    decided, and clearing that one would hide a real ask.
    """
    result = await session.execute(
        update(AgentNotification)
        .where(
            AgentNotification.org_id == org_id,
            AgentNotification.run_id == run_id,
            AgentNotification.kind == kind,
            AgentNotification.status != "resolved",
        )
        .values(status="resolved")
    )
    return int(result.rowcount or 0)


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
    if settings is not None and await _try_notify_workflow(session, org_id, settings, kind, title, body):
        channels.append("workflow")

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
