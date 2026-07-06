"""Regression: PATCH /chat/sessions must be serializable after update.

updated_at is computed server-side (onupdate=func.now()); the flush expires
it, and serializing the ORM row without a refresh lazy-loads OUTSIDE the
async greenlet (MissingGreenlet), 500-ing every chat-history save — the UI
swallowed this and conversations silently never persisted.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.chat import ChatSession
from api.models.org import Org
from api.models.user import UserProfile
from api.repositories.chat import ChatRepository
from api.schemas.chat import ChatSessionRead
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_update_data_result_serializes(admin_session: AsyncSession) -> None:
    org = Org(name=f"Chat-Org-{uuid.uuid4().hex[:6]}", permission_number=7)
    admin_session.add(org)
    user = UserProfile(
        auth_subject=f"chat-owner-{uuid.uuid4()}",
        username=f"chat_owner_{uuid.uuid4().hex[:8]}",
        email=f"chat_owner_{uuid.uuid4().hex[:8]}@test.local",
    )
    admin_session.add(user)
    await admin_session.flush()
    chat = ChatSession(org_id=org.id, user_id=user.id, chat_data={})
    admin_session.add(chat)
    await admin_session.flush()

    repo = ChatRepository(admin_session, org.id)
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    updated = await repo.update_data(chat.id, payload)
    assert updated is not None

    # This is exactly what the router does; before the refresh fix it raised
    # (pydantic get_attribute_error wrapping MissingGreenlet).
    read = ChatSessionRead.model_validate(updated)
    assert read.chat_data == payload

    appended = await repo.append_messages(chat.id, [{"role": "assistant", "content": "hi"}])
    assert appended is not None
    read2 = ChatSessionRead.model_validate(appended)
    assert read2.chat_data is not None
    assert len(read2.chat_data["messages"]) == 2
