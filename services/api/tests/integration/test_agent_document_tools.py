"""Integration tests for the agent's document & folder tools.

Exercises AgentService._dispatch directly (the handlers the LLM invokes) against
a real PostgreSQL, proving the tools act with the CALLER's permissions: admins
can manage folders/permissions, members are limited to what they could do in the
UI, and reads/writes are scoped to folders the user can see. Celery/brain-api
dispatch is stubbed so no broker is required.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.auth.dependencies import CurrentUser, OrgContext
from api.config import Settings
from api.models.document import Document, Folder
from api.models.org import Org, Role
from api.models.user import UserProfile
from api.services.agent import AgentService
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(secret_key="test")  # type: ignore[call-arg]


def _agent(engine: AsyncEngine, ctx: OrgContext) -> AgentService:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return AgentService(ctx.org_id, _settings(), session_factory=factory, org_context=ctx)


async def _seed_user(admin_session: AsyncSession) -> UserProfile:
    """A real user_profiles row so create_document's uploaded_by_id FK resolves."""
    tag = uuid.uuid4().hex[:8]
    user = UserProfile(auth_subject=f"sub-{tag}", username=f"u-{tag}", email=f"u-{tag}@x.com")
    admin_session.add(user)
    await admin_session.commit()
    return user


def _admin_ctx(org_id: uuid.UUID, profile_id: uuid.UUID) -> OrgContext:
    user = CurrentUser(sub="admin", username="admin", email="a@x.com", profile_id=profile_id, is_site_admin=False)
    return OrgContext(user=user, org_id=org_id, membership=MagicMock(), is_org_admin=True)


def _member_ctx(org_id: uuid.UUID, profile_id: uuid.UUID) -> OrgContext:
    # Empty membership → the user's masks match only public (unrestricted) folders.
    user = CurrentUser(sub="member", username="member", email="m@x.com", profile_id=profile_id, is_site_admin=False)
    membership = SimpleNamespace(regions=[], departments=[], roles=[], groups=[])
    return OrgContext(user=user, org_id=org_id, membership=membership, is_org_admin=False)


async def _org(admin_session: AsyncSession) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=f"AGENTDOC-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    return org


@pytest.fixture
def stub_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize Celery/brain-api side effects so document tools need no broker."""
    from api.routers import documents as d
    from api.routers import folders as f

    monkeypatch.setattr(d, "dispatch_ingest", lambda *a, **k: "task")
    monkeypatch.setattr(d, "dispatch_extract_ingest", lambda *a, **k: "task")
    monkeypatch.setattr(d, "dispatch_metadata_update", lambda *a, **k: "task")
    monkeypatch.setattr(f, "dispatch_metadata_update", lambda *a, **k: "task")
    fake_brain = MagicMock()
    fake_brain.remove_document = AsyncMock(return_value={})
    monkeypatch.setattr(d, "BrainAPIClient", lambda settings: fake_brain)


# --------------------------------------------------------------------------- #
# Admin happy path
# --------------------------------------------------------------------------- #
async def test_admin_full_document_lifecycle(
    engine: AsyncEngine, admin_session: AsyncSession, stub_dispatch: None
) -> None:
    org = await _org(admin_session)
    user = await _seed_user(admin_session)
    agent = _agent(engine, _admin_ctx(org.id, user.id))

    folder = await agent._dispatch("create_folder", {"name": "Policies"})
    fid = folder["created_folder"]["id"]

    created = await agent._dispatch(
        "create_document", {"title": "Vacation", "text": "# Vacation\n", "folder_id": fid}
    )
    did = created["created_document"]["id"]

    listing = await agent._dispatch("list_documents", {"folder_id": fid})
    assert any(doc["id"] == did for doc in listing["documents"])

    got = await agent._dispatch("get_document", {"document_id": did})
    assert got["title"] == "Vacation"
    assert "Vacation" in (got["content"] or "")

    renamed = await agent._dispatch("update_document", {"document_id": did, "title": "Vacation Policy"})
    assert renamed["updated_document"]["title"] == "Vacation Policy"

    rewritten = await agent._dispatch("update_document_content", {"document_id": did, "text": "# New body\n"})
    assert rewritten["updated_document"]["status"] == "PENDING"


# --------------------------------------------------------------------------- #
# Admin-only boundary: members cannot manage folders or permissions
# --------------------------------------------------------------------------- #
async def test_member_cannot_create_or_update_folders(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    org = await _org(admin_session)
    user = await _seed_user(admin_session)
    folder = Folder(name="Existing", org_id=org.id, dot_path="Existing")
    admin_session.add(folder)
    await admin_session.commit()

    agent = _agent(engine, _member_ctx(org.id, user.id))

    create = await agent._dispatch("create_folder", {"name": "New"})
    assert "admin" in create["error"].lower()

    update = await agent._dispatch("update_folder", {"folder_id": str(folder.id), "name": "Renamed"})
    assert "admin" in update["error"].lower()


# --------------------------------------------------------------------------- #
# Visibility scoping: a member only sees / acts on folders they can see
# --------------------------------------------------------------------------- #
async def test_member_reads_and_writes_are_visibility_scoped(
    engine: AsyncEngine, admin_session: AsyncSession, stub_dispatch: None
) -> None:
    org = await _org(admin_session)
    user = await _seed_user(admin_session)
    public = Folder(name="Public", org_id=org.id, dot_path="Public")
    restricted = Folder(
        name="Secret",
        org_id=org.id,
        dot_path="Secret",
        viewer_permissions_config=[{"role": "Exec"}],
        view_permission_masks=[999_999],  # a mask the empty-membership member lacks
    )
    admin_session.add_all([public, restricted])
    await admin_session.flush()  # populate folder ids before referencing them
    hidden_doc = Document(title="TopSecret", org_id=org.id, folder_id=restricted.id, text="x")
    admin_session.add(hidden_doc)
    await admin_session.commit()

    agent = _agent(engine, _member_ctx(org.id, user.id))

    names = {f["name"] for f in (await agent._dispatch("list_folders", {}))["folders"]}
    assert "Public" in names
    assert "Secret" not in names

    denied_create = await agent._dispatch(
        "create_document", {"title": "N", "folder_id": str(restricted.id)}
    )
    assert "not visible" in denied_create["error"].lower()

    denied_read = await agent._dispatch("get_document", {"document_id": str(hidden_doc.id)})
    assert "not visible" in denied_read["error"].lower()

    allowed = await agent._dispatch("create_document", {"title": "Note", "folder_id": str(public.id)})
    assert "created_document" in allowed


async def test_member_can_edit_document_in_visible_folder(
    engine: AsyncEngine, admin_session: AsyncSession, stub_dispatch: None
) -> None:
    org = await _org(admin_session)
    user = await _seed_user(admin_session)
    public = Folder(name="Team", org_id=org.id, dot_path="Team")
    admin_session.add(public)
    await admin_session.flush()  # populate folder id before referencing it
    doc = Document(title="Doc", org_id=org.id, folder_id=public.id, text="old body")
    admin_session.add(doc)
    await admin_session.commit()

    agent = _agent(engine, _member_ctx(org.id, user.id))
    result = await agent._dispatch("update_document_content", {"document_id": str(doc.id), "text": "new body"})
    assert result["updated_document"]["status"] == "PENDING"


# --------------------------------------------------------------------------- #
# Admin can set folder permissions (resolving dimension names to masks)
# --------------------------------------------------------------------------- #
async def test_admin_sets_folder_permissions(
    engine: AsyncEngine, admin_session: AsyncSession, stub_dispatch: None
) -> None:
    org = await _org(admin_session)
    user = await _seed_user(admin_session)
    role = Role(name="Manager", org_id=org.id, permission_number=3)
    folder = Folder(name="HR", org_id=org.id, dot_path="HR")
    admin_session.add_all([role, folder])
    await admin_session.commit()

    agent = _agent(engine, _admin_ctx(org.id, user.id))

    dims = await agent._dispatch("list_permission_dimensions", {})
    assert "Manager" in dims["roles"]

    updated = await agent._dispatch(
        "update_folder", {"folder_id": str(folder.id), "viewer_permissions_config": [{"role": "Manager"}]}
    )
    assert "updated_folder" in updated

    await admin_session.refresh(folder)
    assert folder.viewer_permissions_config == [{"role": "Manager"}]
    assert folder.view_permission_masks  # resolved to a non-empty mask list
