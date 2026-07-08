"""Integration: inbound webhook receiver verifies the HMAC signature, starts the
bound workflow with the POST body as input, and drives it inline (immediate).
Unknown/disabled tokens and bad signatures are rejected."""

from __future__ import annotations

import json
import time
import uuid

import pytest
from api.models.org import Org
from api.repositories.workflow import (
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
)
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.crypto import encrypt_secret
from api.services.entity_service import EntityService
from api.services.workflow.inbound import hash_token, trigger_from_inbound
from api.services.workflow.webhook_signing import SignatureError, sign
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_ENC_KEY = "test-inbound-encryption-key"


def test_hash_token_is_deterministic_sha256() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert len(hash_token("x")) == 64


async def _seed(admin_session: AsyncSession):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFIN-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Lead", slug="lead",
            fields=[EntityFieldCreate(name="Score", slug="score", field_type="integer")],
        )
    )
    # A workflow that doubles the inbound body's `score` into a run variable.
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "s", "type": "trigger", "data": {}},
            {"id": "calc", "type": "task",
             "data": {"task_type": "script", "transform": {"doubled": {"*": [{"var": "after.score"}, 2]}}}},
            {"id": "e", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "s", "target": "calc"}, {"id": "e1", "source": "calc", "target": "e"}],
    }
    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    wf = await wf_repo.create(name="Ingest", entity_definition_id=entity.id, description=None)
    version = await ver_repo.create(workflow_id=wf.id, version_number=1, definition=definition)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(wf, enabled=True, active_version_id=version.id)
    await admin_session.commit()
    return org, wf


async def test_inbound_webhook_runs_inline_with_body(admin_session: AsyncSession) -> None:
    """A legacy (unsigned) endpoint still works, and the run executes INLINE — the
    script task's result is present the moment trigger_from_inbound returns, with
    no separate sweep/drive step."""
    org, wf = await _seed(admin_session)
    await set_tenant(admin_session, str(org.id))
    await WorkflowInboundEndpointRepository(admin_session, org.id).create(
        name="zapier", workflow_id=wf.id, token_hash=hash_token("tok_ABC")
    )
    await admin_session.commit()

    result = await trigger_from_inbound(admin_session, "tok_ABC", json.dumps({"score": 21}).encode())
    assert result is not None
    assert result["status"] == "succeeded"  # ran inline, not just seeded
    run_id = uuid.UUID(result["run_id"])
    await admin_session.commit()

    fresh = await WorkflowRunRepository(admin_session, org.id).get_by_id(run_id)
    assert fresh.trigger_operation == "webhook"
    assert (fresh.variables or {}).get("doubled") == 42


async def test_inbound_signed_endpoint_runs_with_valid_signature(admin_session: AsyncSession) -> None:
    org, wf = await _seed(admin_session)
    await set_tenant(admin_session, str(org.id))
    secret = "whsec_" + uuid.uuid4().hex
    await WorkflowInboundEndpointRepository(admin_session, org.id).create(
        name="robot", workflow_id=wf.id, token_hash=hash_token("tok_SIGNED"),
        signing_secret_encrypted=encrypt_secret(secret, _ENC_KEY),
    )
    await admin_session.commit()

    raw = json.dumps({"score": 21}).encode()
    header = sign(secret, raw, timestamp=int(time.time()))
    result = await trigger_from_inbound(
        admin_session, "tok_SIGNED", raw, signature=header, org_encryption_key=_ENC_KEY
    )
    assert result is not None and result["status"] == "succeeded"
    await admin_session.commit()
    fresh = await WorkflowRunRepository(admin_session, org.id).get_by_id(uuid.UUID(result["run_id"]))
    assert (fresh.variables or {}).get("doubled") == 42


async def _make_signed_endpoint(admin_session: AsyncSession, token: str) -> tuple[Org, str]:
    org, wf = await _seed(admin_session)
    await set_tenant(admin_session, str(org.id))
    secret = "whsec_" + uuid.uuid4().hex
    await WorkflowInboundEndpointRepository(admin_session, org.id).create(
        name="signed", workflow_id=wf.id, token_hash=hash_token(token),
        signing_secret_encrypted=encrypt_secret(secret, _ENC_KEY),
    )
    await admin_session.commit()
    return org, secret


async def test_inbound_signed_endpoint_rejects_missing_signature(admin_session: AsyncSession) -> None:
    """A signed endpoint with NO signature header is rejected before any work
    (the router maps SignatureError → 401)."""
    await _make_signed_endpoint(admin_session, "tok_NOSIG")
    with pytest.raises(SignatureError):
        await trigger_from_inbound(
            admin_session, "tok_NOSIG", b'{"score":5}', org_encryption_key=_ENC_KEY
        )


async def test_inbound_signed_endpoint_rejects_tampered_body(admin_session: AsyncSession) -> None:
    """A valid signature for a DIFFERENT body than the one sent is rejected —
    the HMAC binds the signature to the exact payload."""
    _org, secret = await _make_signed_endpoint(admin_session, "tok_TAMPER")
    header = sign(secret, b'{"score":999}', timestamp=int(time.time()))  # signs a different body
    with pytest.raises(SignatureError):
        await trigger_from_inbound(
            admin_session, "tok_TAMPER", b'{"score":5}', signature=header, org_encryption_key=_ENC_KEY
        )


async def test_inbound_unknown_token_returns_none(admin_session: AsyncSession) -> None:
    await _seed(admin_session)
    assert await trigger_from_inbound(admin_session, "not-a-real-token", b'{"x":1}') is None


async def test_inbound_disabled_endpoint_returns_none(admin_session: AsyncSession) -> None:
    org, wf = await _seed(admin_session)
    await set_tenant(admin_session, str(org.id))
    endpoint = await WorkflowInboundEndpointRepository(admin_session, org.id).create(
        name="off", workflow_id=wf.id, token_hash=hash_token("tok_OFF")
    )
    endpoint.enabled = False
    await admin_session.commit()
    assert await trigger_from_inbound(admin_session, "tok_OFF", b"{}") is None

