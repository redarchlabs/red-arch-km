"""Documents that came into a work order, and documents that came out.

An agent could research, plan and report, but everything it produced was prose in
a diary entry — no way to hand back an artifact, and no way to find one a person
had handed in. The order recorded what happened and not what came of it.

``work_order_artifacts`` has existed since migration 030 with no code path; this
is that path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.models.agent import Agent
from api.models.org import Org
from api.repositories.document import DocumentRepository
from api.services.agents.tools.artifacts import (
    ATTACH_DOCUMENT,
    LIST_WORK_ORDER_DOCUMENTS,
    READ_WORK_ORDER_DOCUMENT,
)
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@dataclass
class _Ctx:
    session: Any
    org_id: uuid.UUID
    work_order_id: uuid.UUID | None
    agent: Any = None
    run_id: uuid.UUID | None = None
    settings: Any = None
    actor_user_id: uuid.UUID | None = None
    tool_call_id: str | None = "call_1"
    _extra: dict = field(default_factory=dict)


async def _seed(admin_session: AsyncSession):
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Art-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="analyst", provider="openai", model="m", kind="operator", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(title="Audit the site")
    await admin_session.commit()
    return org, agent, wo, svc


class TestAnAgentHandsSomethingBack:
    async def test_attaching_creates_a_document_and_a_link(self, admin_session: AsyncSession) -> None:
        # A real KM2 document, not a private blob: searchable, permissioned and
        # reprocessable like everything else in the org.
        org, agent, wo, svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)

        out = await ATTACH_DOCUMENT.handler(ctx, {"title": "SEO audit", "content": "# Findings\n\nSlow."})

        assert out["attached"] is True
        document = await DocumentRepository(admin_session, org.id).get(uuid.UUID(out["document_id"]))
        assert document is not None and document.title == "SEO audit"
        artifacts = await svc.list_artifacts(wo.id)
        assert [a.kind for a, _ in artifacts] == ["output"]

    async def test_an_empty_document_is_refused(self, admin_session: AsyncSession) -> None:
        org, agent, wo, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)

        assert "error" in await ATTACH_DOCUMENT.handler(ctx, {"title": "Empty", "content": "  "})
        assert "error" in await ATTACH_DOCUMENT.handler(ctx, {"title": "", "content": "text"})

    async def test_a_run_with_no_work_order_is_told_where_to_go(self, admin_session: AsyncSession) -> None:
        org, agent, _wo, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=None, agent=agent)

        out = await ATTACH_DOCUMENT.handler(ctx, {"title": "T", "content": "c"})

        assert "create_document" in out["error"]


class TestFindingWhatIsThere:
    async def test_inputs_and_outputs_are_distinguishable(self, admin_session: AsyncSession) -> None:
        """An agent starting work wants the inputs; a person reviewing wants the
        outputs. Both are on the order, so the direction has to be readable."""
        org, agent, wo, svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)
        spec = await DocumentRepository(admin_session, org.id).create(title="The spec", text="do this")
        await admin_session.flush()
        await svc.attach_documents(wo.id, [spec.id], kind="input")
        await ATTACH_DOCUMENT.handler(ctx, {"title": "My report", "content": "done"})

        out = await LIST_WORK_ORDER_DOCUMENTS.handler(ctx, {})

        kinds = {d["title"]: d["kind"] for d in out["documents"]}
        assert kinds == {"The spec": "input", "My report": "output"}

    async def test_reading_one_back(self, admin_session: AsyncSession) -> None:
        org, agent, wo, svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)
        spec = await DocumentRepository(admin_session, org.id).create(title="The spec", text="build a thing")
        await admin_session.flush()
        await svc.attach_documents(wo.id, [spec.id], kind="input")

        out = await READ_WORK_ORDER_DOCUMENT.handler(ctx, {"document_id": str(spec.id)})

        assert out["title"] == "The spec"
        assert "build a thing" in out["text"]

    async def test_it_cannot_read_a_document_that_is_not_attached(self, admin_session: AsyncSession) -> None:
        """A work-order tool must not become a way to read any org document by id."""
        org, agent, wo, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)
        elsewhere = await DocumentRepository(admin_session, org.id).create(title="Private", text="secret")
        await admin_session.flush()

        out = await READ_WORK_ORDER_DOCUMENT.handler(ctx, {"document_id": str(elsewhere.id)})

        assert "error" in out and "secret" not in str(out)


class TestAttachingFromAReply:
    async def test_a_reply_can_be_only_an_attachment(self, admin_session: AsyncSession) -> None:
        """"Here, look at this" with a screenshot and no words is a whole message."""
        org, agent, wo, svc = await _seed(admin_session)
        doc = await DocumentRepository(admin_session, org.id).create(title="shot.png", text=None)
        await admin_session.flush()

        await svc.reply(wo.id, "", document_ids=[doc.id])
        await admin_session.commit()

        assert len(await svc.list_artifacts(wo.id)) == 1
        assert any("📎" in e.text for e in (await svc.list_entries_page(wo.id)).entries)

    async def test_an_empty_reply_with_nothing_attached_is_still_refused(
        self, admin_session: AsyncSession
    ) -> None:
        from api.services.agents.work_order_service import WorkOrderValidationError

        org, agent, wo, svc = await _seed(admin_session)

        with pytest.raises(WorkOrderValidationError):
            await svc.reply(wo.id, "   ")

    async def test_an_unknown_document_does_not_lose_the_message(self, admin_session: AsyncSession) -> None:
        """One bad id should not swallow a reply someone typed."""
        org, agent, wo, svc = await _seed(admin_session)

        await svc.reply(wo.id, "still delivered", document_ids=[uuid.uuid4()])
        await admin_session.commit()

        assert any("still delivered" in e.text for e in (await svc.list_entries_page(wo.id)).entries)

    async def test_detaching_leaves_the_document_alone(self, admin_session: AsyncSession) -> None:
        # Attaching to the wrong order should be undoable without destroying work.
        org, agent, wo, svc = await _seed(admin_session)
        doc = await DocumentRepository(admin_session, org.id).create(title="misfiled", text="x")
        await admin_session.flush()
        attached = await svc.attach_documents(wo.id, [doc.id])
        await admin_session.commit()

        await svc.detach_artifact(wo.id, attached[0].id)
        await admin_session.commit()

        assert await svc.list_artifacts(wo.id) == []
        assert await DocumentRepository(admin_session, org.id).get(doc.id) is not None
