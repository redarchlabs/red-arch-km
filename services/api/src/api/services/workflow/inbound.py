"""Inbound webhook receiver: start a workflow run from an external POST.

A public endpoint carries an opaque token; only its SHA-256 hash is stored. When
the endpoint has a signing secret, the request must also carry a valid HMAC
signature (``webhook_signing``) — so a leaked URL alone can't trigger the
workflow. The receiver resolves the endpoint on a privileged session (RLS-
bypassing, so it can find the row without knowing the org), verifies the
signature, then downgrades to the endpoint's org (``SET LOCAL ROLE app_user`` +
tenant GUC) before creating the run — so the run and everything it does are
RLS-enforced for that tenant.

The run is started AND driven to quiescence inline (like a manual run), so the
workflow executes immediately rather than waiting for the next token-engine
sweep — a webhook that speaks to a robot must be real-time. The beat sweep
remains the backstop that resumes anything the run parks on (timer/receive/join).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.repositories.workflow import (
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
)
from api.services.crypto import decrypt_secret
from api.services.workflow.engine import TokenEngine
from api.services.workflow.webhook_signing import verify as verify_signature


def hash_token(token: str) -> str:
    """SHA-256 hex of an inbound token (only the hash is ever stored)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def trigger_from_inbound(
    session: AsyncSession,
    token: str,
    raw_body: bytes,
    *,
    signature: str | None = None,
    org_encryption_key: str = "",
    webhook_allowlist: tuple[str, ...] = (),
    trusted_local_hosts: tuple[str, ...] = (),
    settings: Any = None,
) -> dict[str, Any] | None:
    """Resolve the endpoint by token, verify its signature, and run its workflow.

    ``session`` must be privileged (for the cross-org token lookup); this function
    downgrades to the endpoint's org before any tenant write. ``raw_body`` is the
    EXACT request bytes (the signature is computed over them, and the JSON input
    is parsed from them). Returns ``{"run_id", "status"}``; returns ``None`` if
    the token is unknown/disabled or the workflow has no published version. Raises
    :class:`webhook_signing.SignatureError` when a signature is required but
    missing/invalid (the caller maps that to 401).
    """
    # The token→endpoint lookup spans every org, so opt into the RLS bypass; we
    # downgrade to the endpoint's org (bypass off) below before any tenant write.
    await db_scope.enter_bypass(session)
    endpoint = await WorkflowInboundEndpointRepository(session).get_by_token_hash(hash_token(token))
    if endpoint is None or not endpoint.enabled:
        return None

    # Authenticate the request BEFORE any tenant work. Only endpoints created
    # with a signing secret require this; legacy token-only endpoints skip it.
    if endpoint.signing_secret_encrypted:
        secret = decrypt_secret(endpoint.signing_secret_encrypted, org_encryption_key)
        verify_signature(secret, raw_body, signature)  # raises SignatureError → 401

    # Parse the JSON input from the raw bytes (tolerant: empty on non-JSON).
    try:
        parsed = json.loads(raw_body) if raw_body else {}
    except (ValueError, TypeError):
        parsed = {}
    body: dict[str, Any] = parsed if isinstance(parsed, dict) else {"value": parsed}

    org_id = endpoint.org_id
    workflow_id = endpoint.workflow_id

    # Downgrade to the endpoint's tenant for all subsequent writes (RLS-enforced).
    # The endpoint was resolved cross-org by the caller's bypass session; here we
    # drop to app_user + tenant GUC (bypass off). See api/db_scope.py.
    await db_scope.enter_tenant(session, org_id)

    wf = await WorkflowRepository(session, org_id).get(workflow_id)
    if wf is None or wf.active_version_id is None:
        return None
    version = await WorkflowVersionRepository(session, org_id).get(wf.active_version_id)
    if version is None or version.status != "published":
        return None

    runs = WorkflowRunRepository(session, org_id)
    run = await runs.create_run_if_absent(
        workflow_id=wf.id,
        workflow_version_id=version.id,
        outbox_id=uuid.uuid4(),
        outbox_seq=None,
        created_at=datetime.now(UTC),
        trigger_operation="webhook",
        record_id=None,
        input_snapshot={"before": None, "after": body, "vars": {}},
        depth=0,
    )
    if run is None:
        return None
    engine = TokenEngine(
        session,
        org_encryption_key=org_encryption_key,
        webhook_allowlist=webhook_allowlist,
        trusted_local_hosts=trusted_local_hosts,
        settings=settings,
    )
    await engine.start_run(run, version.definition)
    # Drive inline so the workflow executes NOW (real-time), not on the next
    # sweep. A run that parks (timer/receive) returns here at the park; the beat
    # sweep resumes it. The advisory lock on a brand-new run is uncontended.
    await engine.drive_run(run)
    fresh = await runs.get(run.id, run.created_at)
    return {"run_id": str(run.id), "status": fresh.status if fresh else run.status}
