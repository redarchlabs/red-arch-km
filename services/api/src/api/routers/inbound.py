"""Public inbound-webhook receiver.

``POST /api/inbound/{token}`` starts the workflow bound to that token, with the
JSON body as the run's input. No user auth — the opaque token in the path is the
credential (only its hash is stored). Runs on a privileged session so the token
lookup can find the endpoint across tenants; the service downgrades to the
endpoint's org before any write.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.config import Settings, get_settings
from api.db import get_session_factory
from api.services.workflow.inbound import trigger_from_inbound

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{token}")
async def receive_inbound_webhook(
    token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is fine; treat as empty
        payload = {}
    body = payload if isinstance(payload, dict) else {"value": payload}

    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            result = await trigger_from_inbound(
                session, token, body, org_encryption_key=settings.org_encryption_key.get_secret_value()
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("inbound webhook processing failed")
            raise
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown or disabled endpoint")
    return result
