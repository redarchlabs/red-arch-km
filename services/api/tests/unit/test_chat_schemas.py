"""Tests for chat session schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from api.schemas.chat import (
    ChatData,
    ChatMessage,
    ChatSessionCreate,
    ChatSessionRead,
)


class TestChatMessage:
    def test_user_message(self) -> None:
        msg_id = uuid.uuid4()
        now = datetime.now(UTC)
        msg = ChatMessage(
            id=msg_id,
            role="user",
            content="Hello!",
            timestamp=now,
        )
        assert msg.id == msg_id
        assert msg.role == "user"
        assert msg.content == "Hello!"
        assert msg.timestamp == now
        assert msg.sources == []

    def test_assistant_message_with_sources(self) -> None:
        msg_id = uuid.uuid4()
        now = datetime.now(UTC)
        sources = [{"document_key": "doc-1", "title": "Policy Handbook", "score": 0.9}]
        msg = ChatMessage(
            id=msg_id,
            role="assistant",
            content="Based on the policy...",
            timestamp=now,
            sources=sources,
        )
        assert msg.role == "assistant"
        assert len(msg.sources) == 1
        assert msg.sources[0]["document_key"] == "doc-1"


class TestChatData:
    def test_empty_chat_data(self) -> None:
        data = ChatData()
        assert data.messages == []

    def test_with_messages(self) -> None:
        msg = ChatMessage(
            id=uuid.uuid4(),
            role="user",
            content="Hello",
            timestamp=datetime.now(UTC),
        )
        data = ChatData(messages=[msg])
        assert len(data.messages) == 1
        assert data.messages[0].content == "Hello"


class TestChatSessionCreate:
    def test_empty_create(self) -> None:
        create = ChatSessionCreate()
        assert create.chat_data is None

    def test_with_initial_data(self) -> None:
        create = ChatSessionCreate(chat_data={"messages": []})
        assert create.chat_data == {"messages": []}


class TestChatSessionRead:
    def test_from_attributes(self) -> None:
        # Simulate ORM object with attributes
        class MockSession:
            id = uuid.uuid4()
            chat_data = {"messages": []}
            created_at = datetime.now(UTC)
            updated_at = datetime.now(UTC)

        read = ChatSessionRead.model_validate(MockSession())
        assert read.id == MockSession.id
        assert read.chat_data == {"messages": []}
