"""Inbound webhook receiver: start a workflow run from an external POST.

A public endpoint carries an opaque token; only its SHA-256 hash is stored. The
receiver resolves the endpoint on a privileged session (RLS-bypassing, so it can
find the row without knowing the org), then downgrades to the endpoint's org
(``SET LOCAL ROLE app_user`` + tenant GUC) before creating the run — so the run
and everything it does are RLS-enforced for that tenant. The run is SEEDED here
and driven by the token-engine sweep (the webhook returns immediately).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.workflow import (
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
)
from api.services.workflow.engine import TokenEngine


def hash_token(token: str) -> str:
    """SHA-256 hex of an inbound token (only the hash is ever stored)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def trigger_from_inbound(
    session: AsyncSession,
    token: str,
    body: dict[str, Any] | None,
    *,
    org_encryption_key: str = "",
) -> dict[str, Any] | None:
    """Resolve the endpoint by token and start its workflow with ``body`` as input.

    ``session`` must be privileged (for the cross-org token lookup); this function
    downgrades to the endpoint's org before any tenant write. Returns
    ``{"run_id", "status"}`` or ``None`` if the token is unknown/disabled or the
    workflow has no published version. The run's active tokens are left for the
    sweep to advance.
    """
    endpoint = await WorkflowInboundEndpointRepository(session).get_by_token_hash(hash_token(token))
    if endpoint is None or not endpoint.enabled:
        return None
    org_id = endpoint.org_id
    workflow_id = endpoint.workflow_id

    # Downgrade to the endpoint's tenant for all subsequent writes (RLS-enforced).
    await session.execute(text("SET LOCAL ROLE app_user"))
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(org_id)}
    )

    wf = await WorkflowRepository(session, org_id).get(workflow_id)
    if wf is None or wf.active_version_id is None:
        return None
    version = await WorkflowVersionRepository(session, org_id).get(wf.active_version_id)
    if version is None or version.status != "published":
        return None

    run = await WorkflowRunRepository(session, org_id).create_run_if_absent(
        workflow_id=wf.id,
        workflow_version_id=version.id,
        outbox_id=uuid.uuid4(),
        outbox_seq=None,
        created_at=datetime.now(UTC),
        trigger_operation="webhook",
        record_id=None,
        input_snapshot={"before": None, "after": body or {}, "vars": {}},
        depth=0,
    )
    if run is None:
        return None
    engine = TokenEngine(session, org_encryption_key=org_encryption_key)
    await engine.start_run(run, version.definition)
    return {"run_id": str(run.id), "status": run.status}
