"""Chat session repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.chat import ChatSession


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: uuid.UUID) -> ChatSession | None:
        return await self._session.get(ChatSession, session_id)

    async def list_for_user(
        self, user_id: uuid.UUID, *, include_deleted: bool = False
    ) -> list[ChatSession]:
        query = select(ChatSession).where(ChatSession.user_id == user_id)
        if not include_deleted:
            query = query.where(ChatSession.deleted.is_(False))
        query = query.order_by(ChatSession.updated_at.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        chat_data: dict | None = None,
    ) -> ChatSession:
        session = ChatSession(
            user_id=user_id,
            org_id=org_id,
            chat_data=chat_data or {},
        )
        self._session.add(session)
        await self._session.flush()
        return session

    async def update_data(
        self, session_id: uuid.UUID, chat_data: dict
    ) -> ChatSession | None:
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
