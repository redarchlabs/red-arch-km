"""The chat Assistant's knowledge reads act with the caller's permissions.

``search_knowledge_base``, ``grep_documents`` and ``read_document`` all reach
brain-api, which filters on exactly the masks it is handed. These pin that the
Assistant hands it the CALLER's masks — the same ones the member-facing chat uses —
so a member cannot learn, through the Assistant, what a document they cannot open
says. ``search_knowledge_base`` used to send no masks at all, which brain-api reads
as "unrestricted".

brain-api is faked; the database (memberships, folders, documents) is real.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from api.auth.dependencies import CurrentUser, OrgContext
from api.config import Settings
from api.models.document import Document, Folder
from api.models.org import Org
from api.models.user import UserProfile
from api.services.agent import AgentService
from api.services.search_access import UNRESTRICTED_MASK
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


class _FakeBrain:
    """Records every brain-api call; returns canned results."""

    calls: list[tuple[str, dict[str, Any]]] = []
    read_result: dict[str, Any] | None = None

    def __init__(self, _settings: Settings) -> None: ...

    async def vector_chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("vector_chat", kwargs))
        return {"answer": "ok"}

    async def text_search(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("text_search", kwargs))
        return {"documents_matched": 1, "truncated": False, "documents": [{"document_key": "k", "title": "T"}]}

    async def read_document(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("read_document", kwargs))
        return self.read_result


@pytest.fixture
def brain(monkeypatch: pytest.MonkeyPatch) -> type[_FakeBrain]:
    _FakeBrain.calls = []
    _FakeBrain.read_result = {
        "document_key": "k",
        "title": "Manual",
        "total_chunks": 3,
        "start_chunk": 0,
        "end_chunk": 3,
        "next_chunk": None,
        "text": "the whole manual",
    }
    monkeypatch.setattr("api.services.agent.BrainAPIClient", _FakeBrain)
    return _FakeBrain


def _agent(engine: AsyncEngine, ctx: OrgContext) -> AgentService:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return AgentService(ctx.org_id, Settings(secret_key="test"), session_factory=factory, org_context=ctx)  # type: ignore[call-arg]


def _ctx(org_id: uuid.UUID, profile_id: uuid.UUID, *, admin: bool) -> OrgContext:
    user = CurrentUser(sub="u", username="u", email="u@x.com", profile_id=profile_id, is_site_admin=False)
    membership: Any = MagicMock() if admin else SimpleNamespace(regions=[], departments=[], roles=[], groups=[])
    return OrgContext(user=user, org_id=org_id, membership=membership, is_org_admin=admin)


async def _setup(admin_session: AsyncSession) -> tuple[Org, UserProfile]:
    await set_tenant(admin_session, None)
    org = Org(name=f"KBREAD-{uuid.uuid4().hex[:8]}", permission_number=1)
    tag = uuid.uuid4().hex[:8]
    user = UserProfile(auth_subject=f"sub-{tag}", username=f"u-{tag}", email=f"u-{tag}@x.com")
    admin_session.add_all([org, user])
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    return org, user


async def _document(admin_session: AsyncSession, org: Org, *, folder: Folder | None) -> Document:
    doc = Document(
        title="Manual", org_id=org.id, folder_id=folder.id if folder else None, document_key=f"k-{uuid.uuid4().hex[:6]}"
    )
    admin_session.add(doc)
    await admin_session.commit()
    return doc


class TestMasksReachBrainApi:
    async def test_a_members_search_carries_their_masks(
        self, engine: AsyncEngine, admin_session: AsyncSession, brain: type[_FakeBrain]
    ) -> None:
        """The regression: no masks here means brain-api searches every document."""
        org, user = await _setup(admin_session)
        agent = _agent(engine, _ctx(org.id, user.id, admin=False))

        await agent._dispatch("search_knowledge_base", {"query": "salaries"})
        await agent._dispatch("grep_documents", {"query": "salaries"})
        await agent._dispatch("read_document", {"document_key": "k"})

        assert [name for name, _ in brain.calls] == ["vector_chat", "text_search", "read_document"]
        for _, kwargs in brain.calls:
            keys = kwargs["access_keys"]
            assert keys is not None, "a member must never read unrestricted"
            assert UNRESTRICTED_MASK in keys

    async def test_an_admin_reads_unrestricted(
        self, engine: AsyncEngine, admin_session: AsyncSession, brain: type[_FakeBrain]
    ) -> None:
        org, user = await _setup(admin_session)
        agent = _agent(engine, _ctx(org.id, user.id, admin=True))

        await agent._dispatch("search_knowledge_base", {"query": "x"})
        await agent._dispatch("grep_documents", {"query": "x"})

        assert all(kwargs["access_keys"] is None for _, kwargs in brain.calls)


class TestReadDocument:
    async def test_by_id_resolves_the_key_of_a_visible_document(
        self, engine: AsyncEngine, admin_session: AsyncSession, brain: type[_FakeBrain]
    ) -> None:
        org, user = await _setup(admin_session)
        public = Folder(name="Handbooks", org_id=org.id, dot_path="Handbooks")
        admin_session.add(public)
        await admin_session.commit()
        doc = await _document(admin_session, org, folder=public)
        agent = _agent(engine, _ctx(org.id, user.id, admin=False))

        out = await agent._dispatch("read_document", {"document_id": str(doc.id)})

        assert out["text"] == "the whole manual"
        assert brain.calls[0][1]["document_key"] == doc.document_key

    async def test_by_id_refuses_a_document_the_member_cannot_see(
        self, engine: AsyncEngine, admin_session: AsyncSession, brain: type[_FakeBrain]
    ) -> None:
        """Unfiled documents are admin-only in the app; the id path checks before
        brain-api is ever asked."""
        org, user = await _setup(admin_session)
        doc = await _document(admin_session, org, folder=None)
        agent = _agent(engine, _ctx(org.id, user.id, admin=False))

        out = await agent._dispatch("read_document", {"document_id": str(doc.id)})

        assert "not visible" in out["error"]
        assert brain.calls == []

    async def test_brain_api_refusal_is_reported_as_not_found(
        self, engine: AsyncEngine, admin_session: AsyncSession, brain: type[_FakeBrain]
    ) -> None:
        org, user = await _setup(admin_session)
        brain.read_result = None
        agent = _agent(engine, _ctx(org.id, user.id, admin=False))

        out = await agent._dispatch("read_document", {"document_key": "restricted-doc"})

        assert "not visible" in out["error"]

    async def test_needs_a_key_or_an_id(
        self, engine: AsyncEngine, admin_session: AsyncSession, brain: type[_FakeBrain]
    ) -> None:
        org, user = await _setup(admin_session)
        agent = _agent(engine, _ctx(org.id, user.id, admin=False))

        out = await agent._dispatch("read_document", {})

        assert "required" in out["error"]
        assert brain.calls == []
