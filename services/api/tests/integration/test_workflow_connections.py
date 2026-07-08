"""Integration: connector credentials — RLS isolation, secret confidentiality,
and an authenticated http_request via a resolved connection (mocked transport)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from api.config import Settings
from api.models.org import Org
from api.repositories.workflow import WorkflowConnectionRepository
from api.services.crypto import encrypt_secret
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.runner import ActionExecutor
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_KEY = Settings(secret_key="test").org_encryption_key.get_secret_value()  # type: ignore[call-arg]


async def _org(admin_session: AsyncSession, prefix: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    return org


async def test_connection_secret_is_encrypted_and_resolves(admin_session: AsyncSession) -> None:
    org = await _org(admin_session, "CONN")
    await set_tenant(admin_session, str(org.id))
    repo = WorkflowConnectionRepository(admin_session, org.id)
    conn = await repo.create(
        name="stripe",
        base_url="https://api.example.com",
        auth_type="bearer",
        secret_encrypted=encrypt_secret("sk_live_SECRET", _KEY),
        config={},
    )
    await admin_session.commit()

    # Stored ciphertext must not be the plaintext...
    assert conn.secret_encrypted is not None
    assert "sk_live_SECRET" not in conn.secret_encrypted
    # ...but the runner's resolver decrypts it back.
    executor = ActionExecutor(admin_session, org_encryption_key=_KEY)
    resolved = await executor._resolve_connection(org.id, "stripe")
    assert resolved is not None
    assert resolved.secret == "sk_live_SECRET"  # noqa: S105 - fake secret in a test
    assert resolved.base_url == "https://api.example.com"


async def test_connections_are_rls_isolated(admin_session: AsyncSession, session: AsyncSession) -> None:
    from api.models.workflow import WorkflowConnection
    from sqlalchemy import select

    org_a = await _org(admin_session, "CONNA")
    org_b = await _org(admin_session, "CONNB")

    await set_tenant(admin_session, str(org_a.id))
    await WorkflowConnectionRepository(admin_session, org_a.id).create(
        name="secret-conn", auth_type="bearer", secret_encrypted=encrypt_secret("x", _KEY)
    )
    await admin_session.commit()

    # From the RLS-enforced app_user session: tenant B sees NOTHING (an unfiltered
    # select returns only the current tenant's rows), tenant A sees the one row.
    await set_tenant(session, str(org_b.id))
    rows_b = (await session.execute(select(WorkflowConnection))).scalars().all()
    assert rows_b == []
    await set_tenant(session, str(org_a.id))
    rows_a = (await session.execute(select(WorkflowConnection))).scalars().all()
    assert len(rows_a) == 1 and rows_a[0].name == "secret-conn"


async def test_http_request_injects_auth_and_captures_response(
    admin_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _org(admin_session, "CONNH")
    await set_tenant(admin_session, str(org.id))
    await WorkflowConnectionRepository(admin_session, org.id).create(
        name="api",
        base_url="https://api.example.com",
        auth_type="bearer",
        secret_encrypted=encrypt_secret("sk_live_SECRET", _KEY),
    )
    await admin_session.commit()

    captured: dict = {}

    class _FakeResp:
        status_code = 201
        is_success = True
        text = ""

        def json(self) -> dict:
            return {"id": "ch_1", "ok": True}

    class _FakeClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def request(self, method: str, url: str, headers: dict | None = None, json: object = None) -> _FakeResp:
            captured.update(method=method, url=url, headers=headers or {}, json=json)
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    executor = ActionExecutor(
        admin_session, webhook_allowlist=("api.example.com",), org_encryption_key=_KEY
    )
    result = await executor.execute(
        org_id=org.id,
        action_type="http_request",
        config={"connection": "api", "method": "POST", "path": "charges", "body": {"amount": 100}},
        record_id=None,
        before=None,
        after=None,
        entity_definition_id=None,
        origin_run_id=uuid.uuid4(),
    )

    assert result.ok, result.error
    # The decrypted secret was injected as the bearer token...
    assert captured["headers"]["Authorization"] == "Bearer sk_live_SECRET"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.example.com/charges"
    assert captured["json"] == {"amount": 100}
    # ...the response was captured, and the secret never appears in the output.
    assert result.output == {"status_code": 201, "ok": True, "body": {"id": "ch_1", "ok": True}}
    assert "sk_live_SECRET" not in str(result.output)


async def test_http_request_enforces_ssrf_allowlist(admin_session: AsyncSession) -> None:
    org = await _org(admin_session, "CONNS")
    await set_tenant(admin_session, str(org.id))
    await WorkflowConnectionRepository(admin_session, org.id).create(
        name="evil", base_url="https://169.254.169.254", auth_type="none"
    )
    await admin_session.commit()

    # Empty allowlist ⇒ deny-by-default; the metadata IP is also a private host.
    executor = ActionExecutor(admin_session, webhook_allowlist=(), org_encryption_key=_KEY)
    result = await executor.execute(
        org_id=org.id,
        action_type="http_request",
        config={"connection": "evil", "method": "GET", "path": "latest/meta-data"},
        record_id=None,
        before=None,
        after=None,
        entity_definition_id=None,
        origin_run_id=uuid.uuid4(),
    )
    assert result.ok is False
    assert "allow-listed" in (result.error or "")


async def test_manual_run_legacy_walker_resolves_connection(
    admin_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A MANUAL run of a LEGACY (schema_version 1) workflow whose http_request uses
    a named connection must resolve it. Regression: the dispatcher's legacy-walker
    path (``_run_actions``) omitted the connection resolver and failed with
    "connections are not available in this context" — while the runner/token path
    resolved it. This is exactly the path a form/view button hits via POST /run."""
    org = await _org(admin_session, "CONNMANUAL")
    await set_tenant(admin_session, str(org.id))
    await WorkflowConnectionRepository(admin_session, org.id).create(
        name="robot", base_url="https://robot.example.com", auth_type="none"
    )
    await admin_session.commit()

    captured: dict = {}

    class _FakeResp:
        status_code = 200
        is_success = True
        text = ""

        def json(self) -> dict:
            return {"ok": True, "gesture": "nod"}

    class _FakeClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def request(self, method: str, url: str, headers: dict | None = None, json: object = None) -> _FakeResp:
            captured.update(method=method, url=url)
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    # No schema_version ⇒ the legacy walker runs this on the manual path.
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
            {
                "id": "a",
                "type": "action",
                "data": {
                    "action_type": "http_request",
                    "config": {"connection": "robot", "method": "POST", "path": "/gesture", "body": {"name": "nod"}},
                },
            },
        ],
        "edges": [{"id": "e1", "source": "t", "target": "a"}],
    }
    svc = WorkflowService(admin_session, org.id)
    workflow = await svc.create_workflow(name="RobotManual", entity_definition_id=None, description=None)
    version = await svc.save_draft(workflow.id, definition)
    await svc.publish(workflow.id, version.id)
    await admin_session.commit()

    dispatcher = WorkflowDispatchService(
        admin_session, webhook_allowlist=("robot.example.com",), org_encryption_key=_KEY
    )
    run, executed = await dispatcher.run_version_manually(
        org.id,
        workflow,
        version,
        operation="update",
        record_id=None,
        before={"x": "a"},
        after={"x": "b"},
    )
    await admin_session.commit()

    # Before the fix this raised "connections are not available in this context".
    assert run.status == "succeeded", run.error
    assert executed == 1
    assert captured["url"] == "https://robot.example.com/gesture"
