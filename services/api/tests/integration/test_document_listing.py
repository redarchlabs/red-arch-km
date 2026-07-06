"""Integration tests for document listing visibility.

Regression coverage for the NULL-folder invisibility bug: a document created
without a folder (folder_id IS NULL) must still be listable, and folder-scoped
listing must return exactly that folder's contents. Exercised against a real
PostgreSQL (RLS) via the shared testcontainers fixtures.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.document import Document, Folder
from api.models.org import Org
from api.repositories.document import DocumentRepository
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create an org with one folder + one filed doc + one unfiled doc.

    Uses the RLS-enforced ``session`` fixture (app_user role) so that the
    repository queries under test are naturally scoped to this tenant and do
    not see documents committed by sibling tests.

    Everything is flushed (not committed) inside a single transaction so the
    transaction-local ``app.current_tenant_id`` GUC stays in effect for the
    queries the tests run afterwards — a commit would clear it and RLS would
    then hide all rows.
    """
    org = Org(name=f"DocList-{uuid.uuid4().hex[:8]}", permission_number=1)
    session.add(org)
    await session.flush()  # orgs is not RLS-scoped; no tenant needed yet

    await set_tenant(session, str(org.id))
    folder = Folder(name="Reports", org_id=org.id, dot_path="Reports")
    session.add(folder)
    await session.flush()

    filed = Document(title="Filed", org_id=org.id, folder_id=folder.id, text="a")
    unfiled = Document(title="Unfiled", org_id=org.id, folder_id=None, text="b")
    session.add_all([filed, unfiled])
    await session.flush()
    return org.id, folder.id


class TestDocumentListing:
    async def test_unfiled_docs_visible_when_include_unfiled(self, session: AsyncSession) -> None:
        """The general list (include_unfiled=True) surfaces NULL-folder docs."""
        org_id, folder_id = await _seed(session)
        repo = DocumentRepository(session, org_id)

        docs, total = await repo.list_for_folders(folder_ids=[folder_id], include_unfiled=True)
        titles = {d.title for d in docs}
        assert titles == {"Filed", "Unfiled"}
        assert total == 2

    async def test_unfiled_docs_hidden_without_include(self, session: AsyncSession) -> None:
        """Legacy behaviour: without include_unfiled, NULL-folder docs vanish.

        This is the bug we are guarding against — it must NOT be how the router
        lists the general view.
        """
        org_id, folder_id = await _seed(session)
        repo = DocumentRepository(session, org_id)

        docs, total = await repo.list_for_folders(folder_ids=[folder_id], include_unfiled=False)
        titles = {d.title for d in docs}
        assert titles == {"Filed"}
        assert total == 1

    async def test_folder_scoped_excludes_unfiled(self, session: AsyncSession) -> None:
        """Folder browse (a single folder) returns only that folder's docs."""
        org_id, folder_id = await _seed(session)
        repo = DocumentRepository(session, org_id)

        docs, total = await repo.list_for_folders(folder_ids=[folder_id], include_unfiled=False)
        assert {d.title for d in docs} == {"Filed"}
        assert total == 1

    async def test_empty_folder_set_still_shows_unfiled(self, session: AsyncSession) -> None:
        """A brand-new org with no folders must still show pasted (unfiled) docs.

        Without the fix, folder_ids=[] means `folder_id IN ()` — nothing — and
        the pasted document is invisible to everyone, including admins.
        """
        org_id, _ = await _seed(session)
        repo = DocumentRepository(session, org_id)

        docs, total = await repo.list_for_folders(folder_ids=[], include_unfiled=True)
        assert {d.title for d in docs} == {"Unfiled"}
        assert total == 1
