"""The reply route hands the service everything the caller sent.

Caught live: the router was calling ``reply(wo_id, body.text, actor_profile_id=…)``
and silently dropping ``document_ids``, so a pasted screenshot uploaded, showed a
chip, cleared on send — and never attached to anything. Every service-level test
passed, because the service was fine. This tests the wiring, which is where it
broke.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.routers.work_orders import router as work_orders_router
from api.services.agents.work_order_service import WorkOrderService
from fastapi import FastAPI

pytestmark = pytest.mark.unit

WO_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()
PROFILE_ID = uuid.uuid4()


class _Wo:
    """Only the fields WorkOrderRead reads."""

    id = WO_ID
    slug = "wo"
    title = "T"
    status = "in_progress"
    body = None
    priority = "normal"
    mode = "manual"
    review_level = "standard"
    assigned_agent_id = None
    created_by_profile_id = None
    created_at = updated_at = __import__("datetime").datetime(2026, 8, 9, tzinfo=__import__("datetime").UTC)


def _app(seen: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    app.include_router(work_orders_router, prefix="/api/work-orders")
    app.dependency_overrides[get_tenant_db] = lambda: AsyncMock()
    app.dependency_overrides[require_org_admin] = lambda: OrgContext(
        org_id=ORG_ID,
        user=type("U", (), {"profile_id": PROFILE_ID})(),
        membership=None,
        is_org_admin=True,
    )

    async def _reply(self: Any, wo_id: Any, text: str, **kwargs: Any) -> Any:
        seen.update({"wo_id": wo_id, "text": text, **kwargs})
        return _Wo()

    WorkOrderService.reply = _reply  # type: ignore[method-assign]
    return app


async def test_the_attachments_reach_the_service() -> None:
    seen: dict[str, Any] = {}
    doc = uuid.uuid4()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(seen)), base_url="http://test") as client:
        resp = await client.post(f"/api/work-orders/{WO_ID}/reply", json={"text": "look", "document_ids": [str(doc)]})

    assert resp.status_code == 200
    assert seen["document_ids"] == [doc]
    assert seen["text"] == "look"


async def test_a_reply_with_no_attachments_sends_an_empty_list() -> None:
    # Never None: the service treats "nothing attached" and "field missing" the
    # same way only because this is always a list.
    seen: dict[str, Any] = {}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(seen)), base_url="http://test") as client:
        resp = await client.post(f"/api/work-orders/{WO_ID}/reply", json={"text": "just words"})

    assert resp.status_code == 200
    assert seen["document_ids"] == []
