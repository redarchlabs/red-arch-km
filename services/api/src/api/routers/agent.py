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

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.db import get_session_factory
from api.models.org import Org
from api.services.agent import AgentService, apply_tenant_scope
from api.services.crypto import decrypt_secret

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
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    # Deliberately NOT depending on get_db: a request-scoped session would stay
    # open (pinning a pool connection) for the whole SSE stream — the entire
    # multi-iteration LLM loop. Instead we do a short-lived read for the org key
    # here, then hand AgentService the session factory so it opens a fresh
    # short-lived session per tool call and commits per tool (Finding 2). These
    # sessions keep the privileged get_db role (the assistant runs schema DDL);
    # see AgentService.apply_tenant_scope for why RLS/app_user is not used.
    factory = get_session_factory(settings)
    org_key: str | None = None
    async with factory() as session:
        await apply_tenant_scope(session, ctx.org_id)
        org = (await session.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one_or_none()
        stored = org.openai_api_key if org else None
        # Stored encrypted at rest (services/crypto.py); decrypt for the client.
        if stored:
            org_key = decrypt_secret(stored, settings.org_encryption_key.get_secret_value())

    agent = AgentService(ctx.org_id, settings, session_factory=factory, org_openai_key=org_key)
    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def iterator() -> AsyncGenerator[bytes]:
        try:
            async for event in agent.run_stream(history):
                yield f"data: {json.dumps(event, default=str)}\n\n".encode()
        except Exception:  # noqa: BLE001 - never break the SSE frame contract
            # Per-tool sessions are already committed/rolled back inside the
            # loop; there is no request-scoped transaction to unwind here.
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
