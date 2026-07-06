"""Regression: tenant-bound repositories isolate by org_id WITHOUT relying on RLS.

The production API historically connected as a superuser, which silently
bypasses PostgreSQL RLS — so org isolation must not depend on RLS alone. These
tests run every repository query on ``admin_session`` (the testcontainers
superuser, which bypasses RLS even under FORCE ROW LEVEL SECURITY) and assert
that a repository bound to org A never returns org B's rows. If the explicit
``org_id`` filter regresses, RLS being off here means the leak is caught.

Complements test_rls_isolation.py (which proves the DB-level RLS backstop) and
test_chat_sessions.py (which exercises both layers together).
"""

from __future__ import annotations

import uuid

import pytest
from api.models.chat import ChatSession
from api.models.document import Document, Folder, Tag
from api.models.org import Org
from api.models.user import UserProfile
from api.repositories.chat import ChatRepository
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.repositories.tag import TagRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_two_orgs(admin_session: AsyncSession) -> tuple[Org, Org, UserProfile]:
    """Create two orgs (A and B), each with a document, folder, tag, and chat
    session, plus a user that owns the chat sessions. Committed via the
    superuser session so RLS never scopes the seed."""
    org_a = Org(name=f"ScopeA-{uuid.uuid4().hex[:8]}", permission_number=1)
    org_b = Org(name=f"ScopeB-{uuid.uuid4().hex[:8]}", permission_number=1)
    user = UserProfile(
        auth_subject=f"scope-{uuid.uuid4()}",
        username=f"scope_{uuid.uuid4().hex[:8]}",
        email=f"scope_{uuid.uuid4().hex[:8]}@test.local",
    )
    admin_session.add_all([org_a, org_b, user])
    await admin_session.flush()

    for org in (org_a, org_b):
        admin_session.add_all(
            [
                Folder(name="Reports", org_id=org.id, dot_path="Reports"),
                Tag(name="urgent", org_id=org.id),
                Document(title="Doc", org_id=org.id, folder_id=None, text="x"),
                ChatSession(org_id=org.id, user_id=user.id, chat_data={"messages": []}),
            ]
        )
    await admin_session.commit()
    return org_a, org_b, user


async def _row_ids(admin_session: AsyncSession, model, org_id: uuid.UUID) -> uuid.UUID:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    result = await admin_session.execute(select(model.id).where(model.org_id == org_id))
    return result.scalars().first()


class TestRepositoryOrgScopingWithoutRLS:
    async def test_document_repo_cannot_read_other_org(self, admin_session: AsyncSession) -> None:
        org_a, org_b, _ = await _seed_two_orgs(admin_session)
        b_doc_id = await _row_ids(admin_session, Document, org_b.id)

        repo_a = DocumentRepository(admin_session, org_a.id)
        # Direct cross-org fetch is filtered out even though RLS is bypassed here.
        assert await repo_a.get(b_doc_id) is None
        # Listing returns only org A's docs.
        docs, total = await repo_a.list_for_folders(folder_ids=None)
        assert total == 1
        assert {d.org_id for d in docs} == {org_a.id}

    async def test_folder_repo_cannot_read_other_org(self, admin_session: AsyncSession) -> None:
        org_a, org_b, _ = await _seed_two_orgs(admin_session)
        b_folder_id = await _row_ids(admin_session, Folder, org_b.id)

        repo_a = FolderRepository(admin_session, org_a.id)
        assert await repo_a.get(b_folder_id) is None
        folders, total = await repo_a.list_visible_to_masks(user_masks=None)
        assert total == 1
        assert {f.org_id for f in folders} == {org_a.id}

    async def test_tag_repo_cannot_read_other_org(self, admin_session: AsyncSession) -> None:
        org_a, org_b, _ = await _seed_two_orgs(admin_session)
        b_tag_id = await _row_ids(admin_session, Tag, org_b.id)

        repo_a = TagRepository(admin_session, org_a.id)
        assert await repo_a.get(b_tag_id) is None
        tags, total = await repo_a.list_all()
        assert total == 1
        assert {t.org_id for t in tags} == {org_a.id}

    async def test_chat_repo_cannot_read_other_org(self, admin_session: AsyncSession) -> None:
        org_a, org_b, user = await _seed_two_orgs(admin_session)
        b_chat_id = await _row_ids(admin_session, ChatSession, org_b.id)

        repo_a = ChatRepository(admin_session, org_a.id)
        assert await repo_a.get(b_chat_id) is None
        sessions, total = await repo_a.list_for_user(user.id)
        assert total == 1
        assert {s.org_id for s in sessions} == {org_a.id}
