"""Integration test for the entity/field access-control policy against a real DB.

Proves the tamper-proofing end-to-end through the live catalog + RLS + physical
tables: a non-privileged (member) caller can neither read a ``server_only`` field
(a quiz answer key) nor write a ``workflow_only`` entity (a certification), while a
privileged caller (the workflow engine / an org admin) can do both. ``privileged``
is app-level authorization and is orthogonal to RLS tenant isolation.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import (
    DynamicEntityRepository,
    EntityRecordError,
    RecordAccessError,
)
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def _load_repo(
    session: AsyncSession, org_id: uuid.UUID, definition_id: uuid.UUID, *, privileged: bool = False
) -> DynamicEntityRepository:
    definition = await EntityDefinitionRepository(session, org_id).get(definition_id)
    assert definition is not None
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition_id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition_id)
    return DynamicEntityRepository(session, org_id, definition, fields, rels, privileged=privileged)


class TestServerOnlyFieldIntegration:
    async def test_answer_key_hidden_from_member_visible_to_workflow(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "ACL-FIELD")
        await set_tenant(admin_session, str(org.id))
        svc = EntityService(admin_session, org.id)
        question = await svc.create_definition(
            EntityDefinitionCreate(
                name="Question",
                slug="question",
                fields=[
                    EntityFieldCreate(name="Prompt", slug="prompt", field_type="text"),
                    EntityFieldCreate(
                        name="Correct answer",
                        slug="correct_answer",
                        field_type="text",
                        read_access="server_only",
                    ),
                ],
            )
        )
        await admin_session.commit()

        await set_tenant(session, str(org.id))
        # The workflow engine / admin (privileged) seeds the answer key.
        priv = await _load_repo(session, org.id, question.id, privileged=True)
        created = await priv.create({"prompt": "2+2?", "correct_answer": "4"})
        await session.commit()
        assert created["correct_answer"] == "4"  # privileged create returns it

        member = await _load_repo(session, org.id, question.id)  # non-privileged
        # Read strips the answer key.
        got = await member.get(uuid.UUID(str(created["id"])))
        assert got is not None
        assert got["prompt"] == "2+2?"
        assert "correct_answer" not in got
        # List strips it too.
        rows, _ = await member.list()
        assert rows and all("correct_answer" not in r for r in rows)
        # And it can't be used as a filter oracle.
        with pytest.raises(EntityRecordError):
            await member.list(filters=[("correct_answer", "eq", "4")])

        # The grading workflow (privileged) still reads it — grade_quiz depends on this.
        priv_read = await _load_repo(session, org.id, question.id, privileged=True)
        got_priv = await priv_read.get(uuid.UUID(str(created["id"])))
        assert got_priv is not None and got_priv["correct_answer"] == "4"
        # (A member also can't SET the protected field — covered by the unit test
        # test_member_write_drops_server_only_field.)


class TestWorkflowOnlyEntityIntegration:
    async def test_certification_forge_blocked_for_member(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "ACL-ENTITY")
        await set_tenant(admin_session, str(org.id))
        svc = EntityService(admin_session, org.id)
        cert = await svc.create_definition(
            EntityDefinitionCreate(
                name="Certification",
                slug="certification",
                write_access="workflow_only",
                fields=[EntityFieldCreate(name="Passed", slug="passed", field_type="boolean")],
            )
        )
        await admin_session.commit()

        await set_tenant(session, str(org.id))
        member = await _load_repo(session, org.id, cert.id)  # non-privileged
        # A learner can't forge a passing certificate.
        with pytest.raises(RecordAccessError):
            await member.create({"passed": True})

        # The grading workflow (privileged) issues it legitimately.
        priv = await _load_repo(session, org.id, cert.id, privileged=True)
        issued = await priv.create({"passed": True})
        await session.commit()
        rid = uuid.UUID(str(issued["id"]))

        # And a member can neither tamper with nor delete an issued cert.
        with pytest.raises(RecordAccessError):
            await member.update(rid, {"passed": False})
        with pytest.raises(RecordAccessError):
            await member.delete(rid)

        # It's still readable (only writes are gated) — a learner can view their cert.
        got = await member.get(rid)
        assert got is not None and got["passed"] is True
