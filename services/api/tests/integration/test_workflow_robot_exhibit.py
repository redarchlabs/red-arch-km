"""Integration: a KM2 workflow drives a physical robot end-to-end — the design proof.

A signed 'heard' webhook triggers a workflow that (1) retrieves from the knowledge base,
(2) uses the new constrained-LLM steering node ``llm_decide`` to choose a spoken reply + an
in-vocabulary gesture, (3) commands a robot bridge over authenticated HTTP, and (4) loops
under an exclusive gateway until the LLM signals ``done`` — a bounded, goal-directed
controller with the LLM steering *within* its rails.

Everything here is the REAL token engine, real action handlers, real capture/templating,
real HMAC signature, and a real HTTP call to a captured robot bridge. Only the two network
leaves — the KB lookup and the LLM call — are stubbed at the ActionExecutor boundary (their
own unit tests cover them), so the proof is deterministic and needs no OpenAI/brain-api.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from api.models.org import Org
from api.models.workflow import WorkflowInboundEndpoint
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
)
from api.services.crypto import encrypt_secret
from api.services.workflow.inbound import hash_token, trigger_from_inbound
from api.services.workflow.runner import ActionExecutor
from api.services.workflow.webhook_signing import SignatureError, sign
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

# The robot's advertised vocabulary (would come from GET /capabilities).
GESTURES = ["nod", "greet", "celebrate", "think", "wiggle"]
MOODS = ["calm", "happy", "curious", "excited"]

ENC_KEY = "test-org-encryption-key"
WEBHOOK_SECRET = "robot-heard-signing-secret"  # the robot's KM2_WEBHOOK_SECRET
COMMAND_KEY = "robot-command-plane-secret"  # the robot's KM2_COMMAND_SECRET
KB_ANSWER = "The Sun is a star about 1.4 million kilometers across."


class _Bridge(BaseHTTPRequestHandler):
    """A stand-in for the robot bridge: records the authenticated commands it receives."""

    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b""
        _Bridge.received.append(
            {
                "path": self.path,
                "command_key": self.headers.get("X-KM2-Command-Key"),
                "body": json.loads(raw or b"{}"),
            }
        )
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args) -> None:  # silence the test server
        pass


@pytest.fixture()
def robot_bridge():
    _Bridge.received = []
    server = HTTPServer(("127.0.0.1", 0), _Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], _Bridge.received
    finally:
        server.shutdown()


def _definition(port: int, *, synthesize: bool = True) -> dict:
    """The exhibit workflow: heard → KB → llm_decide → say + gesture → gateway(done)→ loop|end.

    ``synthesize=False`` uses the retrieval-only KB path (raw passages, no brain-api
    LLM) so llm_decide does the single grounded generation — one LLM call per turn."""
    robot = f"http://127.0.0.1:{port}"
    hdr = {"X-KM2-Command-Key": COMMAND_KEY}
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {"source": "webhook"}},
            {
                "id": "kb",
                "type": "task",
                "data": {
                    "task_type": "service",
                    "action_type": "knowledge_search",
                    "capture": "kb",
                    "config": {"query": "{{after.text}}", "synthesize": synthesize},
                },
            },
            {
                "id": "decide",
                "type": "task",
                "data": {
                    "task_type": "service",
                    "action_type": "llm_decide",
                    "capture": "decision",
                    "config": {
                        "question": "{{after.text}}",
                        "context": "{{vars.kb.answer}}",
                        "system": "You are a friendly space-museum robot for kids. Stay on space.",
                        "gestures": GESTURES,
                        "moods": MOODS,
                    },
                },
            },
            {
                "id": "say",
                "type": "task",
                "data": {
                    "task_type": "service",
                    "action_type": "http_request",
                    "config": {"method": "POST", "url": f"{robot}/say", "headers": hdr, "body": {"text": "{{vars.decision.say}}"}},
                },
            },
            {
                "id": "gesture",
                "type": "task",
                "data": {
                    "task_type": "service",
                    "action_type": "http_request",
                    "config": {"method": "POST", "url": f"{robot}/gesture", "headers": hdr, "body": {"name": "{{vars.decision.gesture}}"}},
                },
            },
            {
                "id": "gw",
                "type": "gateway",
                "data": {"gateway_type": "exclusive", "expr": {"==": [{"var": "vars.decision.done"}, True]}},
            },
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "kb"},
            {"id": "e1", "source": "kb", "target": "decide"},
            {"id": "e2", "source": "decide", "target": "say"},
            {"id": "e3", "source": "say", "target": "gesture"},
            {"id": "e4", "source": "gesture", "target": "gw"},
            {"id": "e5", "source": "gw", "target": "end", "source_handle": "true"},
            {"id": "e6", "source": "gw", "target": "decide", "source_handle": "default"},  # loop until done
        ],
    }


async def _publish_exhibit(admin_session: AsyncSession, definition: dict) -> str:
    """Create an exhibit org, publish the workflow, and register a signed inbound endpoint.

    Returns the (unique) inbound token to trigger it with."""
    token = f"exhibit-{uuid.uuid4().hex}"  # unique per test (rows are committed to the shared container)
    await set_tenant(admin_session, None)
    org = Org(name=f"Exhibit-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    wf = await WorkflowRepository(admin_session, org.id).create(
        name="Robot conversation", entity_definition_id=None, description=None
    )
    version = await WorkflowVersionRepository(admin_session, org.id).create(
        workflow_id=wf.id, version_number=1, definition=definition
    )
    version.status = "published"
    version.published_at = func.now()
    await WorkflowRepository(admin_session, org.id).update(wf, enabled=True, active_version_id=version.id)

    admin_session.add(
        WorkflowInboundEndpoint(
            name="heard",
            token_hash=hash_token(token),
            workflow_id=wf.id,
            enabled=True,
            signing_secret_encrypted=encrypt_secret(WEBHOOK_SECRET, ENC_KEY),
            org_id=org.id,
        )
    )
    await admin_session.commit()
    return token


async def _fire(admin_session: AsyncSession, token: str, signature: str | None = None) -> dict | None:
    raw = json.dumps({"text": "how big is the sun?"}).encode("utf-8")
    if signature is None:
        signature = sign(WEBHOOK_SECRET, raw, timestamp=int(time.time()))
    return await trigger_from_inbound(
        admin_session,
        token,
        raw,
        signature=signature,
        org_encryption_key=ENC_KEY,
        trusted_local_hosts=("127.0.0.1",),
        settings=None,
    )


async def test_workflow_drives_robot_end_to_end(
    admin_session: AsyncSession, robot_bridge, monkeypatch
) -> None:
    """Happy path: signed heard → KB → llm_decide → authenticated /say + /gesture → done."""
    port, received = robot_bridge

    async def _fake_search(self, org_id, query):  # noqa: ANN001
        assert query == "how big is the sun?"  # templated from {{after.text}}
        return {"answer": KB_ANSWER, "sources": []}

    async def _fake_decide(self, org_id, opts):  # noqa: ANN001
        # The steering LLM sees the rules of engagement + KB context (proves wiring).
        assert opts["context"] == KB_ANSWER
        assert "space-museum robot" in opts["system"]
        return {"say": "The Sun is huge — about 1.4 million kilometers across!", "gesture": "celebrate", "mood": "excited", "done": True, "reason": "answered"}

    monkeypatch.setattr(ActionExecutor, "_search_knowledge", _fake_search)
    monkeypatch.setattr(ActionExecutor, "_decide", _fake_decide)

    token = await _publish_exhibit(admin_session, _definition(port))
    result = await _fire(admin_session, token)

    assert result is not None and result["status"] == "succeeded"
    # The robot received exactly the LLM-chosen speech + gesture, each authenticated.
    assert len(received) == 2
    say = next(r for r in received if r["path"] == "/say")
    gesture = next(r for r in received if r["path"] == "/gesture")
    assert say["body"] == {"text": "The Sun is huge — about 1.4 million kilometers across!"}
    assert say["command_key"] == COMMAND_KEY  # control plane authenticated
    # Steering within rails: the commanded gesture is a member of the robot's vocabulary.
    assert gesture["body"]["name"] == "celebrate" and gesture["body"]["name"] in GESTURES
    assert gesture["command_key"] == COMMAND_KEY


async def test_retrieval_only_single_llm_path_drives_robot(
    admin_session: AsyncSession, robot_bridge, monkeypatch
) -> None:
    """The deployed shape: knowledge_search(synthesize=False) retrieves raw passages
    (NO brain-api LLM) and llm_decide grounds on them and does the ONE generation —
    one LLM call per turn instead of two. The robot still speaks a grounded reply."""
    port, received = robot_bridge
    passages = "[1] Sun Facts\nThe Sun is about 1.4 million kilometers across."

    async def _boom_search(self, org_id, query):  # noqa: ANN001 - synthesis path must NOT be used
        raise AssertionError("synthesize:false must not call the RAG-synthesis path")

    async def _fake_retrieve(self, org_id, query):  # noqa: ANN001
        assert query == "how big is the sun?"  # templated from {{after.text}}
        return {"answer": passages, "sources": [{"number": 1}], "passages": []}

    async def _fake_decide(self, org_id, opts):  # noqa: ANN001
        # llm_decide sees the RAW retrieved passages as context (not a pre-synthesized answer).
        assert opts["context"] == passages
        return {"say": "The Sun is about 1.4 million kilometers across!", "gesture": "nod", "mood": "curious", "done": True, "reason": "grounded"}

    monkeypatch.setattr(ActionExecutor, "_search_knowledge", _boom_search)
    monkeypatch.setattr(ActionExecutor, "_retrieve_knowledge", _fake_retrieve)
    monkeypatch.setattr(ActionExecutor, "_decide", _fake_decide)

    token = await _publish_exhibit(admin_session, _definition(port, synthesize=False))
    result = await _fire(admin_session, token)

    assert result is not None and result["status"] == "succeeded"
    say = next(r for r in received if r["path"] == "/say")
    assert say["body"] == {"text": "The Sun is about 1.4 million kilometers across!"}
    assert say["command_key"] == COMMAND_KEY


async def test_bounded_goal_loop_until_done(
    admin_session: AsyncSession, robot_bridge, monkeypatch
) -> None:
    """Goal-directed: the exclusive gateway loops back to the LLM until it signals done,
    then terminates — bounded, no runaway."""
    port, received = robot_bridge
    calls = {"n": 0}

    async def _fake_search(self, org_id, query):  # noqa: ANN001
        return {"answer": KB_ANSWER, "sources": []}

    async def _fake_decide(self, org_id, opts):  # noqa: ANN001
        calls["n"] += 1
        return {"say": f"turn {calls['n']}", "gesture": "nod", "mood": "curious", "done": calls["n"] >= 3, "reason": "x"}

    monkeypatch.setattr(ActionExecutor, "_search_knowledge", _fake_search)
    monkeypatch.setattr(ActionExecutor, "_decide", _fake_decide)

    token = await _publish_exhibit(admin_session, _definition(port))
    result = await _fire(admin_session, token)

    assert result is not None and result["status"] == "succeeded"
    assert calls["n"] == 3  # steered three times, then the gateway routed to end on done=True
    says = [r for r in received if r["path"] == "/say"]
    assert [s["body"]["text"] for s in says] == ["turn 1", "turn 2", "turn 3"]


async def test_forged_trigger_is_rejected(
    admin_session: AsyncSession, robot_bridge, monkeypatch
) -> None:
    """A heard event with a bad HMAC signature never triggers the workflow (control on the
    trigger side, mirroring the command-plane auth on the robot side)."""
    port, received = robot_bridge
    monkeypatch.setattr(ActionExecutor, "_search_knowledge", lambda self, o, q: {"answer": "", "sources": []})
    token = await _publish_exhibit(admin_session, _definition(port))
    with pytest.raises(SignatureError):
        await _fire(admin_session, token, signature="t=1,v1=deadbeef")
    assert received == []  # nothing reached the robot
