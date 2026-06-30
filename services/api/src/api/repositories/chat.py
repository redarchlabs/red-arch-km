"""Chat session repository."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.chat import ChatSession


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: uuid.UUID) -> ChatSession | None:
        return await self._session.get(ChatSession, session_id)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        offset: int = 0,
        limit: int = 200,
    ) -> tuple[list[ChatSession], int]:
        """Return a page of chat sessions + total count."""
        base = select(ChatSession).where(ChatSession.user_id == user_id)
        if not include_deleted:
            base = base.where(ChatSession.deleted.is_(False))

        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self._session.execute(base.order_by(ChatSession.updated_at.desc()).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        chat_data: dict[str, Any] | None = None,
    ) -> ChatSession:
        session = ChatSession(
            user_id=user_id,
            org_id=org_id,
            chat_data=chat_data or {},
        )
        self._session.add(session)
        await self._session.flush()
        return session

    async def update_data(self, session_id: uuid.UUID, chat_data: dict[str, Any]) -> ChatSession | None:
        chat = await self.get(session_id)
        if chat is None:
            return None
        chat.chat_data = chat_data
        await self._session.flush()
        return chat

    async def soft_delete(self, session_id: uuid.UUID) -> bool:
        chat = await self.get(session_id)
        if chat is None:
            return False
        chat.deleted = True
        await self._session.flush()
        return True

    async def append_messages(self, session_id: uuid.UUID, messages: list[dict[str, Any]]) -> ChatSession | None:
        """Append messages to the session's chat_data."""
        chat = await self.get(session_id)
        if chat is None:
            return None

        current_data = chat.chat_data or {}
        current_messages = current_data.get("messages", [])
        current_messages.extend(messages)
        current_data["messages"] = current_messages

        # SQLAlchemy won't detect in-place JSONB mutation, so reassign
        chat.chat_data = current_data
        await self._session.flush()
        return chat
