"""Tests for chat session schemas."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.schemas.chat import (
    AskRequest,
    ChatData,
    ChatMessage,
    ChatSessionCreate,
    ChatSessionRead,
    ContextFilters,
)


class TestContextFilters:
    def test_empty_filters(self) -> None:
        filters = ContextFilters()
        assert filters.folder_ids == []
        assert filters.tag_ids == []
        assert filters.document_keys == []

    def test_with_values(self) -> None:
        folder_id = uuid.uuid4()
        tag_id = uuid.uuid4()
        filters = ContextFilters(
            folder_ids=[folder_id],
            tag_ids=[tag_id],
            document_keys=["doc-key-1", "doc-key-2"],
        )
        assert filters.folder_ids == [folder_id]
        assert filters.tag_ids == [tag_id]
        assert filters.document_keys == ["doc-key-1", "doc-key-2"]


class TestAskRequest:
    def test_minimal_request(self) -> None:
        req = AskRequest(query="What is the policy?")
        assert req.query == "What is the policy?"
        assert req.context_filters is None

    def test_with_context_filters(self) -> None:
        folder_id = uuid.uuid4()
        req = AskRequest(
            query="Tell me about budgets",
            context_filters=ContextFilters(folder_ids=[folder_id]),
        )
        assert req.query == "Tell me about budgets"
        assert req.context_filters is not None
        assert req.context_filters.folder_ids == [folder_id]

    def test_empty_query_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AskRequest(query="")
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_query_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AskRequest(query="x" * 5001)
        assert "String should have at most 5000 characters" in str(exc_info.value)


class TestChatMessage:
    def test_user_message(self) -> None:
        msg_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc)
        sources = [
            {"document_key": "doc-1", "title": "Policy Handbook", "score": 0.9}
        ]
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
            timestamp=datetime.now(timezone.utc),
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
            created_at = datetime.now(timezone.utc)
            updated_at = datetime.now(timezone.utc)

        read = ChatSessionRead.model_validate(MockSession())
        assert read.id == MockSession.id
        assert read.chat_data == {"messages": []}
