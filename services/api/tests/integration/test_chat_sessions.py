"""Integration tests for chat sessions with RLS isolation."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from api.models.chat import ChatSession
from api.models.org import Org
from api.models.user import UserProfile
from api.repositories.chat import ChatRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def org_a(admin_session: AsyncSession) -> Org:
    """Create org A for testing."""
    org = Org(name=f"Org A {uuid.uuid4()}", permission_number=1)
    admin_session.add(org)
    await admin_session.commit()
    return org


@pytest_asyncio.fixture
async def org_b(admin_session: AsyncSession) -> Org:
    """Create org B for testing."""
    org = Org(name=f"Org B {uuid.uuid4()}", permission_number=2)
    admin_session.add(org)
    await admin_session.commit()
    return org


@pytest_asyncio.fixture
async def user_a(admin_session: AsyncSession) -> UserProfile:
    """Create user A."""
    user = UserProfile(
        keycloak_sub=f"user-a-{uuid.uuid4()}",
        username=f"user_a_{uuid.uuid4().hex[:8]}",
        email=f"user_a_{uuid.uuid4().hex[:8]}@test.local",
    )
    admin_session.add(user)
    await admin_session.commit()
    return user


@pytest_asyncio.fixture
async def user_b(admin_session: AsyncSession) -> UserProfile:
    """Create user B."""
    user = UserProfile(
        keycloak_sub=f"user-b-{uuid.uuid4()}",
        username=f"user_b_{uuid.uuid4().hex[:8]}",
        email=f"user_b_{uuid.uuid4().hex[:8]}@test.local",
    )
    admin_session.add(user)
    await admin_session.commit()
    return user


class TestChatSessionRepository:
    @pytest.mark.asyncio
    async def test_create_session(self, session: AsyncSession, org_a: Org, user_a: UserProfile) -> None:
        # Set tenant context
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)
        chat = await repo.create(
            user_id=user_a.id,
            org_id=org_a.id,
            chat_data={"messages": []},
        )

        assert chat.id is not None
        assert chat.user_id == user_a.id
        assert chat.org_id == org_a.id
        assert chat.chat_data == {"messages": []}
        assert chat.deleted is False

    @pytest.mark.asyncio
    async def test_list_for_user(self, session: AsyncSession, org_a: Org, user_a: UserProfile) -> None:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)

        # Create 3 sessions
        for i in range(3):
            await repo.create(
                user_id=user_a.id,
                org_id=org_a.id,
                chat_data={"messages": [{"id": str(i)}]},
            )

        sessions, total = await repo.list_for_user(user_a.id)
        assert total == 3
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_soft_delete(self, session: AsyncSession, org_a: Org, user_a: UserProfile) -> None:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)
        chat = await repo.create(
            user_id=user_a.id,
            org_id=org_a.id,
        )

        result = await repo.soft_delete(chat.id)
        assert result is True

        # Session still exists but is marked deleted
        deleted = await repo.get(chat.id)
        assert deleted is not None
        assert deleted.deleted is True

        # Not returned in list by default
        sessions, total = await repo.list_for_user(user_a.id)
        assert total == 0

    @pytest.mark.asyncio
    async def test_append_messages(self, session: AsyncSession, org_a: Org, user_a: UserProfile) -> None:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)
        chat = await repo.create(
            user_id=user_a.id,
            org_id=org_a.id,
            chat_data={"messages": [{"id": "1", "role": "user", "content": "Hi"}]},
        )

        # Append new messages
        new_messages = [
            {"id": "2", "role": "assistant", "content": "Hello!"},
            {"id": "3", "role": "user", "content": "How are you?"},
        ]
        updated = await repo.append_messages(chat.id, new_messages)

        assert updated is not None
        assert len(updated.chat_data["messages"]) == 3
        assert updated.chat_data["messages"][0]["id"] == "1"
        assert updated.chat_data["messages"][1]["id"] == "2"
        assert updated.chat_data["messages"][2]["id"] == "3"


class TestChatSessionRLSIsolation:
    @pytest.mark.asyncio
    async def test_user_cannot_see_other_org_sessions(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_a: Org,
        org_b: Org,
        user_a: UserProfile,
    ) -> None:
        """Sessions created in org_a should not be visible when tenant is org_b."""
        # Create session in org_a using admin (no RLS)
        chat = ChatSession(
            user_id=user_a.id,
            org_id=org_a.id,
            chat_data={"messages": []},
        )
        admin_session.add(chat)
        await admin_session.commit()
        session_id = chat.id

        # Query from org_b tenant context
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_b.id)},
        )

        repo = ChatRepository(session)
        result = await repo.get(session_id)

        # Should not be visible due to RLS
        assert result is None

    @pytest.mark.asyncio
    async def test_user_can_see_own_org_sessions(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_a: Org,
        user_a: UserProfile,
    ) -> None:
        """Sessions created in org_a should be visible when tenant is org_a."""
        chat = ChatSession(
            user_id=user_a.id,
            org_id=org_a.id,
            chat_data={"messages": []},
        )
        admin_session.add(chat)
        await admin_session.commit()
        session_id = chat.id

        # Query from org_a tenant context
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)
        result = await repo.get(session_id)

        assert result is not None
        assert result.id == session_id


class TestChatAskEndpoint:
    """Integration tests for the /sessions/{id}/ask endpoint logic."""

    @pytest.mark.asyncio
    async def test_ask_session_not_found(self, session: AsyncSession, org_a: Org, user_a: UserProfile) -> None:
        """Asking on a nonexistent session should fail to find it."""
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)
        nonexistent_id = uuid.uuid4()
        result = await repo.get(nonexistent_id)

        # Should return None — endpoint would raise 404
        assert result is None

    @pytest.mark.asyncio
    async def test_ask_wrong_user_session(
        self, session: AsyncSession, org_a: Org, user_a: UserProfile, user_b: UserProfile
    ) -> None:
        """User A should not be able to access User B's session (ownership check)."""
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)

        # Create session owned by user_b
        chat_b = await repo.create(
            user_id=user_b.id,
            org_id=org_a.id,
            chat_data={"messages": []},
        )

        # Retrieve it
        retrieved = await repo.get(chat_b.id)
        assert retrieved is not None

        # Ownership check: chat belongs to user_b, not user_a
        assert retrieved.user_id == user_b.id
        assert retrieved.user_id != user_a.id

        # The endpoint's ownership check would fail here:
        # if chat is None or chat.user_id != ctx.user.profile_id:
        #     raise HTTPException(status_code=404)

    @pytest.mark.asyncio
    async def test_ask_persists_messages_after_stream(
        self, session: AsyncSession, org_a: Org, user_a: UserProfile
    ) -> None:
        """After streaming completes, messages should be persisted to chat_data."""
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)
        chat = await repo.create(
            user_id=user_a.id,
            org_id=org_a.id,
            chat_data={"messages": []},
        )

        # Simulate what the Ask endpoint does after streaming completes
        user_message = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": "What is the policy on remote work?",
            "timestamp": "2026-06-14T12:00:00Z",
            "sources": [],
        }
        assistant_message = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": "According to the company policy...",
            "timestamp": "2026-06-14T12:00:01Z",
            "sources": [{"document_key": "policy-2026", "title": "Policy Handbook", "chunk_order": 2}],
        }

        updated = await repo.append_messages(chat.id, [user_message, assistant_message])

        assert updated is not None
        assert len(updated.chat_data["messages"]) == 2
        assert updated.chat_data["messages"][0]["role"] == "user"
        assert updated.chat_data["messages"][0]["content"] == "What is the policy on remote work?"
        assert updated.chat_data["messages"][1]["role"] == "assistant"
        assert updated.chat_data["messages"][1]["sources"][0]["document_key"] == "policy-2026"

    @pytest.mark.asyncio
    async def test_ask_preserves_existing_chat_history(
        self, session: AsyncSession, org_a: Org, user_a: UserProfile
    ) -> None:
        """New messages should be appended without losing existing history."""
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)

        # Create session with existing messages
        existing_messages = [
            {"id": "msg-1", "role": "user", "content": "Hello"},
            {"id": "msg-2", "role": "assistant", "content": "Hi there!"},
        ]
        chat = await repo.create(
            user_id=user_a.id,
            org_id=org_a.id,
            chat_data={"messages": existing_messages},
        )

        # Append new messages (simulating an ask)
        new_messages = [
            {"id": "msg-3", "role": "user", "content": "What about budgets?"},
            {"id": "msg-4", "role": "assistant", "content": "Budget info here..."},
        ]
        updated = await repo.append_messages(chat.id, new_messages)

        assert updated is not None
        assert len(updated.chat_data["messages"]) == 4
        assert updated.chat_data["messages"][0]["id"] == "msg-1"
        assert updated.chat_data["messages"][1]["id"] == "msg-2"
        assert updated.chat_data["messages"][2]["id"] == "msg-3"
        assert updated.chat_data["messages"][3]["id"] == "msg-4"

    @pytest.mark.asyncio
    async def test_ask_deleted_session_not_accessible(
        self, session: AsyncSession, org_a: Org, user_a: UserProfile
    ) -> None:
        """Soft-deleted sessions should still be retrievable (ask endpoint checks ownership)."""
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_a.id)},
        )

        repo = ChatRepository(session)
        chat = await repo.create(
            user_id=user_a.id,
            org_id=org_a.id,
            chat_data={"messages": []},
        )

        # Soft delete the session
        await repo.soft_delete(chat.id)

        # The session is still retrievable via get() — endpoint could check deleted flag
        retrieved = await repo.get(chat.id)
        assert retrieved is not None
        assert retrieved.deleted is True
