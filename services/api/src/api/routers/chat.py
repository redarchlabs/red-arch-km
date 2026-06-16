"""Chat session routes."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.org import Org
from api.repositories.chat import ChatRepository
from api.schemas.chat import AskRequest, ChatSessionCreate, ChatSessionRead, ChatSessionUpdate
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.services.brain_client import BrainAPIClient
from api.services.permission_config import calculate_user_masks_from_membership

logger = logging.getLogger(__name__)
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


@router.post("/sessions/{session_id}/ask")
async def ask(
    session_id: uuid.UUID,
    body: AskRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Ask a question in a chat session and stream the RAG response.

    Returns Server-Sent Events with:
    - type="sources": Retrieved document references
    - type="graph": Knowledge graph context
    - type="delta": Streaming answer chunks
    - type="done": Completion marker
    - type="error": Error marker
    """
    # Verify session ownership
    repo = ChatRepository(session)
    chat = await repo.get(session_id)
    if chat is None or chat.user_id != ctx.user.profile_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Get org's permission_number for mask calculation
    org_result = await session.execute(select(Org.permission_number).where(Org.id == ctx.org_id))
    org_number = org_result.scalar_one_or_none()
    if org_number is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Organization configuration error",
        )

    # Calculate user's access masks from their membership dimensions
    access_keys = calculate_user_masks_from_membership(ctx.membership, org_number)

    # Build chat history from existing messages
    chat_data = chat.chat_data or {}
    existing_messages = chat_data.get("messages", [])
    chat_history = [
        {"role": msg.get("role", "user"), "content": msg.get("content", "")}
        for msg in existing_messages[-10:]  # Last 10 messages for context
    ]

    # Resolve tag names if tag_ids provided (for brain-api filter)
    tags: list[str] = []
    if body.context_filters and body.context_filters.tag_ids:
        from api.models.document import Tag

        tag_result = await session.execute(
            select(Tag.name).where(Tag.id.in_(body.context_filters.tag_ids))
        )
        tags = [row[0] for row in tag_result.all()]

    # Create brain API client
    client = BrainAPIClient(settings)

    # Prepare messages to append after streaming
    user_message_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async def stream_and_persist() -> AsyncGenerator[bytes, None]:
        """Stream from brain-api and persist messages after completion."""
        accumulated_content = ""
        sources: list[dict[str, Any]] = []

        try:
            async for chunk in client.vector_chat_stream(
                tenant_id=str(ctx.org_id),
                query=body.query,
                chat_history=chat_history,
                access_keys=access_keys,
                tags=tags if tags else None,
                use_knowledge_graph=True,
            ):
                # Forward raw bytes to client
                yield chunk

                # Parse chunk to accumulate answer and sources
                try:
                    # SSE format: "data: {...}\n\n"
                    chunk_str = chunk.decode("utf-8")
                    for line in chunk_str.split("\n"):
                        if line.startswith("data: "):
                            event_data = json.loads(line[6:])
                            event_type = event_data.get("type")

                            if event_type == "delta":
                                accumulated_content += event_data.get("content", "")
                            elif event_type == "sources":
                                sources = event_data.get("sources", [])
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Non-JSON chunk or partial data; skip parsing
                    pass

        except Exception as e:
            logger.error("RAG streaming failed for session %s: %s", session_id, e)
            error_event = {"type": "error", "message": "Streaming failed"}
            yield f"data: {json.dumps(error_event)}\n\n".encode()
            return

        # Persist messages to database after stream completes
        try:
            new_messages = [
                {
                    "id": str(user_message_id),
                    "role": "user",
                    "content": body.query,
                    "timestamp": now.isoformat(),
                    "sources": [],
                },
                {
                    "id": str(assistant_message_id),
                    "role": "assistant",
                    "content": accumulated_content,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sources": sources,
                },
            ]
            await repo.append_messages(session_id, new_messages)
            await session.commit()
        except Exception as e:
            logger.error("Failed to persist chat messages for session %s: %s", session_id, e)
            # Don't fail the response; messages are already streamed

    return StreamingResponse(
        stream_and_persist(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
