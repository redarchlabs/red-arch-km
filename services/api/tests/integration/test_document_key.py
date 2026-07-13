"""Integration tests for caller-supplied ``document_key`` (real PostgreSQL/RLS).

Guards the chat-citation 404 fix: a document created with an explicit
``document_key`` must persist that exact key and be resolvable via
``get_by_key`` (the path the document page + chat citations use). Keys are
unique per org, but the same key may be reused across different orgs.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.document import DocumentRepository
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org(session: AsyncSession, prefix: str) -> uuid.UUID:
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}", permission_number=1)
    session.add(org)
    await session.flush()
    await set_tenant(session, str(org.id))
    return org.id


class TestExplicitDocumentKey:
    async def test_explicit_key_persists_and_resolves(self, session: AsyncSession) -> None:
        org_id = await _org(session, "DocKey")
        repo = DocumentRepository(session, org_id)

        doc = await repo.create(title="PII", text="body", document_key="privacy-02-pii")
        assert doc.document_key == "privacy-02-pii"

        resolved = await repo.get_by_key("privacy-02-pii")
        assert resolved is not None
        assert resolved.id == doc.id

    async def test_omitted_key_defaults_to_uuid(self, session: AsyncSession) -> None:
        org_id = await _org(session, "DocKey")
        repo = DocumentRepository(session, org_id)

        doc = await repo.create(title="No key", text="body")
        # A valid UUID was auto-assigned (back-compat with the old behaviour).
        uuid.UUID(doc.document_key)

    async def test_duplicate_key_in_same_org_violates_constraint(self, session: AsyncSession) -> None:
        org_id = await _org(session, "DocKey")
        repo = DocumentRepository(session, org_id)

        await repo.create(title="First", text="a", document_key="dup-slug")
        with pytest.raises(IntegrityError):
            await repo.create(title="Second", text="b", document_key="dup-slug")

    async def test_same_key_allowed_across_orgs(self, session: AsyncSession) -> None:
        """Uniqueness is per-org; brain-api scopes by tenant, so two orgs may
        each own a ``privacy-02-pii`` without colliding."""
        org_a = await _org(session, "DocKeyA")
        doc_a = await DocumentRepository(session, org_a).create(
            title="A", text="a", document_key="privacy-02-pii"
        )

        org_b = await _org(session, "DocKeyB")
        doc_b = await DocumentRepository(session, org_b).create(
            title="B", text="b", document_key="privacy-02-pii"
        )

        assert doc_a.document_key == doc_b.document_key == "privacy-02-pii"
        assert doc_a.id != doc_b.id
