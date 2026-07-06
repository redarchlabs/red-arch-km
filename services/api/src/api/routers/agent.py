"""AI configuration-assistant chat with tool-calling.

Runs the agent loop in-process on the privileged session (it can create entities
and workflows) and streams events over SSE. Gated on org admin because its tools
mutate workspace configuration.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.models.org import Org
from api.services.agent import AgentService

router = APIRouter()


class AgentMessage(BaseModel):
    role: str
    content: str


class AgentChatRequest(BaseModel):
    messages: list[AgentMessage] = Field(default_factory=list)


@router.post("/chat/stream")
async def agent_chat_stream(
    body: AgentChatRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    org = (await session.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one_or_none()
    org_key = org.openai_api_key if org else None
    agent = AgentService(session, ctx.org_id, settings, org_openai_key=org_key)
    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def iterator() -> AsyncGenerator[bytes]:
        try:
            async for event in agent.run_stream(history):
                yield f"data: {json.dumps(event, default=str)}\n\n".encode()
            # Commit any tool-driven DB changes once the run completes cleanly.
            await session.commit()
        except Exception:  # noqa: BLE001 - never break the SSE frame contract
            await session.rollback()
            yield b'data: {"type": "error", "error": "Stream failed"}\n\n'

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
