"""Factory for a fully-wired :class:`WorkflowDispatchService`.

The dispatcher needs a bundle of settings-derived collaborators (SSRF allow-lists,
the org encryption key, an email sender, the public base URL). Centralising that
wiring here means every caller that runs a workflow — inline record-change
dispatch, the internal workflows router, and the public ``/api/v1`` workflow
surface — constructs an identically-configured dispatcher instead of copying the
argument list.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.services.email import EmailSender
from api.services.workflow.dispatcher import WorkflowDispatchService


def build_dispatch_service(session: AsyncSession, settings: Settings) -> WorkflowDispatchService:
    """Construct a :class:`WorkflowDispatchService` wired from ``settings``."""
    return WorkflowDispatchService(
        session,
        webhook_allowlist=tuple(settings.workflow_webhook_allowlist or ()),
        trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
        public_base_url=settings.public_base_url,
        email_sender=EmailSender(settings),
        org_encryption_key=settings.org_encryption_key.get_secret_value(),
        settings=settings,
    )
