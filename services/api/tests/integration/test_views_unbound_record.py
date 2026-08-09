"""An unbound view still knows which record it was asked about.

A view with no ``entity_definition_id`` has no *entity* record to load — but that
is not the same as not being about anything. A work-order view is about exactly
one order, named in its URL, and its elements (``agent_timeline``, ``agent_diary``,
``approval_queue``) find that order through the render payload's ``record_id``.

Dropping the id on the unbound branch meant every one of those elements rendered
"nothing selected" for a page whose URL named precisely what it wanted.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.schemas.form import FormConfig
from api.schemas.view import ViewCreate
from api.services.view_service import ViewService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org(admin_session: AsyncSession) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=f"Unbound-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.commit()
    return org


def _work_order_view() -> ViewCreate:
    """The shape the work-order page is composed from: elements that bind to
    ``work_order_id: null``, meaning "whatever this page is about"."""
    return ViewCreate(
        name="Work Order",
        slug=f"wo-{uuid.uuid4().hex[:6]}",
        config=FormConfig.model_validate(
            {
                "version": 1,
                "elements": [
                    {"id": "t", "type": "agent_timeline", "work_order_id": None},
                    {"id": "d", "type": "agent_diary", "work_order_id": None},
                ],
            }
        ),
    )


class TestUnboundViewRecord:
    async def test_the_requested_record_comes_back(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        svc = ViewService(admin_session, org.id)
        view = await svc.create_view(_work_order_view())
        await admin_session.commit()
        wanted = uuid.uuid4()

        render = await svc.render(view.id, wanted)

        assert render.record_id == wanted
        # Still unbound: there is no entity behind it, and nothing was loaded.
        assert render.root_entity_id is None
        assert render.values == {}

    async def test_no_record_asked_for_is_still_none(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        svc = ViewService(admin_session, org.id)
        view = await svc.create_view(_work_order_view())
        await admin_session.commit()

        render = await svc.render(view.id, None)

        assert render.record_id is None

    async def test_the_element_tree_survives_the_round_trip(self, admin_session: AsyncSession) -> None:
        """The elements are what read ``record_id``; a config that lost them on
        save would make the binding moot."""
        org = await _org(admin_session)
        svc = ViewService(admin_session, org.id)
        view = await svc.create_view(_work_order_view())
        await admin_session.commit()

        render = await svc.render(view.id, uuid.uuid4())

        assert [e.type for e in render.config.elements] == ["agent_timeline", "agent_diary"]
