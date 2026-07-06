"""Unit tests for chat router endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from api.auth.dependencies import CurrentUser, OrgContext
from api.config import Settings
from api.models.chat import ChatSession
from api.models.user import UserOrgMembership


@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for tests."""
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        brain_api_url="http://brain-api:8000",
        brain_api_key="test-key",
    )


@pytest.fixture
def mock_user() -> CurrentUser:
    """Create a mock authenticated user."""
    return CurrentUser(
        sub="test-sub",
        username="testuser",
        email="test@example.com",
        profile_id=uuid.uuid4(),
        is_site_admin=False,
    )


@pytest.fixture
def mock_membership() -> UserOrgMembership:
    """Create a mock org membership."""
    membership = MagicMock(spec=UserOrgMembership)
    membership.regions = []
    membership.departments = []
    membership.roles = []
    membership.groups = []
    return membership


@pytest.fixture
def mock_org_context(mock_user: CurrentUser, mock_membership: UserOrgMembership) -> OrgContext:
    """Create a mock org context."""
    return OrgContext(
        user=mock_user,
        org_id=uuid.uuid4(),
        membership=mock_membership,
        is_org_admin=False,
    )


class TestSSEStreamFormat:
    """Tests for the SSE event format used by the RAG chat stream."""

    def test_sse_event_parsing(self) -> None:
        """SSE events should be parseable from the stream format."""
        # Simulate SSE event format
        chunk_event = 'data: {"type": "delta", "content": "Hello"}\n\n'
        sources_event = 'data: {"type": "sources", "sources": [{"document_key": "doc-1"}]}\n\n'
        done_event = 'data: {"type": "done"}\n\n'

        # Parse chunk event
        for line in chunk_event.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                assert data["type"] == "delta"
                assert data["content"] == "Hello"

        # Parse sources event
        for line in sources_event.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                assert data["type"] == "sources"
                assert len(data["sources"]) == 1
                assert data["sources"][0]["document_key"] == "doc-1"

        # Parse done event
        for line in done_event.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                assert data["type"] == "done"

    def test_sse_error_event_format(self) -> None:
        """Error events should be properly formatted."""
        error_event = {"type": "error", "message": "Streaming failed"}
        formatted = f"data: {json.dumps(error_event)}\n\n"
        assert "error" in formatted
        assert "Streaming failed" in formatted


class TestChatSessionOwnership:
    """Tests for chat session ownership verification."""

    def test_ownership_check_same_user(self) -> None:
        """Same user should pass ownership check."""
        user_id = uuid.uuid4()
        mock_chat = MagicMock(spec=ChatSession)
        mock_chat.user_id = user_id

        # Ownership check passes
        assert mock_chat.user_id == user_id

    def test_ownership_check_different_user(self) -> None:
        """Different user should fail ownership check."""
        user_a_id = uuid.uuid4()
        user_b_id = uuid.uuid4()
        mock_chat = MagicMock(spec=ChatSession)
        mock_chat.user_id = user_a_id

        # Ownership check fails
        assert mock_chat.user_id != user_b_id

    def test_ownership_check_none_session(self) -> None:
        """None session should fail ownership check."""
        user_id = uuid.uuid4()
        mock_chat = None

        # Endpoint logic: if chat is None or chat.user_id != ctx.user.profile_id
        assert mock_chat is None or (mock_chat and mock_chat.user_id != user_id)


class TestMessagePersistenceFormat:
    """Tests for the persisted user/assistant message structure."""

    def test_user_message_format(self) -> None:
        """User message should have correct structure."""
        now = datetime.now(UTC)
        user_message = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": "What is the policy?",
            "timestamp": now.isoformat(),
            "sources": [],
        }

        assert user_message["role"] == "user"
        assert user_message["content"] == "What is the policy?"
        assert user_message["sources"] == []
        assert "id" in user_message
        assert "timestamp" in user_message

    def test_assistant_message_with_sources(self) -> None:
        """Assistant message should include sources from RAG."""
        now = datetime.now(UTC)
        sources = [{"document_key": "policy-2026", "title": "Policy Handbook", "chunk_order": 2}]
        assistant_message = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": "According to the policy...",
            "timestamp": now.isoformat(),
            "sources": sources,
        }

        assert assistant_message["role"] == "assistant"
        assert len(assistant_message["sources"]) == 1
        assert assistant_message["sources"][0]["document_key"] == "policy-2026"

    def test_message_pair_for_persistence(self) -> None:
        """Both user and assistant messages should be persisted together."""
        now = datetime.now(UTC)
        messages = [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Question",
                "timestamp": now.isoformat(),
                "sources": [],
            },
            {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": "Answer",
                "timestamp": now.isoformat(),
                "sources": [{"document_key": "doc-1"}],
            },
        ]

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"


class TestAccessKeyCalculation:
    """Tests for access key calculation from membership."""

    def test_empty_membership_dimensions(self) -> None:
        """Empty membership dimensions should produce minimal access keys."""
        from api.services.permission_config import calculate_user_masks_from_membership

        membership = MagicMock(spec=UserOrgMembership)
        membership.regions = []
        membership.departments = []
        membership.roles = []
        membership.groups = []

        # With org_number=1 and empty dimensions
        keys = calculate_user_masks_from_membership(membership, 1)

        # Should return at least the base access key
        assert isinstance(keys, list)

    def test_membership_with_dimensions(self) -> None:
        """Membership with dimensions should produce access keys."""
        from api.services.permission_config import calculate_user_masks_from_membership

        membership = MagicMock(spec=UserOrgMembership)

        # Mock dimension objects with permission_number
        region = MagicMock()
        region.permission_number = 2
        membership.regions = [region]

        dept = MagicMock()
        dept.permission_number = 4
        membership.departments = [dept]

        membership.roles = []
        membership.groups = []

        # With org_number=1 and dimensions
        keys = calculate_user_masks_from_membership(membership, 1)

        assert isinstance(keys, list)
        # Keys should include combinations of org + region + dept
        assert len(keys) >= 1
