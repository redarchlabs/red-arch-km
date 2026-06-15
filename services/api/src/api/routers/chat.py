"""Chat session routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.dependencies import get_tenant_db
from api.repositories.chat import ChatRepository
from api.schemas.chat import ChatSessionCreate, ChatSessionRead, ChatSessionUpdate
from api.schemas.common import PaginatedResponse, PaginationParams, make_page

router = APIRouter()


@router.get("/sessions", response_model=PaginatedResponse[ChatSessionRead])
async def list_sessions(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[ChatSessionRead]:
    repo = ChatRepository(session)
    sessions, total = await repo.list_for_user(
        ctx.user.profile_id,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return make_page([ChatSessionRead.model_validate(s) for s in sessions], total, pagination)


@router.post("/sessions", response_model=ChatSessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: ChatSessionCreate,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ChatSessionRead:
    repo = ChatRepository(session)
    chat = await repo.create(
        user_id=ctx.user.profile_id,
        org_id=ctx.org_id,
        chat_data=body.chat_data,
    )
    return ChatSessionRead.model_validate(chat)


@router.get("/sessions/{session_id}", response_model=ChatSessionRead)
async def get_session(
    session_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ChatSessionRead:
    repo = ChatRepository(session)
    chat = await repo.get(session_id)
    if chat is None or chat.user_id != ctx.user.profile_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return ChatSessionRead.model_validate(chat)


@router.patch("/sessions/{session_id}", response_model=ChatSessionRead)
async def update_session(
    session_id: uuid.UUID,
    body: ChatSessionUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ChatSessionRead:
    repo = ChatRepository(session)
    chat = await repo.get(session_id)
    if chat is None or chat.user_id != ctx.user.profile_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    updated = await repo.update_data(session_id, body.chat_data)
    return ChatSessionRead.model_validate(updated)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    repo = ChatRepository(session)
    chat = await repo.get(session_id)
    if chat is None or chat.user_id != ctx.user.profile_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await repo.soft_delete(session_id)
