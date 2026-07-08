"""Public inbound-webhook receiver.

``POST /api/inbound/{token}`` starts (and runs) the workflow bound to that token,
with the JSON body as the run's input. The opaque token in the path selects the
endpoint; when the endpoint has a signing secret, a valid ``X-KM2-Signature``
HMAC header is also required (only a holder of the secret can trigger it). Runs
on a privileged session so the token lookup can find the endpoint across tenants;
the service verifies the signature, then downgrades to the endpoint's org before
any write, and drives the run inline so it executes immediately.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.config import Settings, get_settings
from api.db import get_session_factory
from api.services.workflow.inbound import trigger_from_inbound
from api.services.workflow.webhook_signing import SIGNATURE_HEADER, SignatureError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{token}")
async def receive_inbound_webhook(
    token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    # Read the EXACT bytes: the HMAC is computed over them, and the JSON input is
    # parsed from them (re-serializing would break signature verification).
    raw_body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)

    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            result = await trigger_from_inbound(
                session,
                token,
                raw_body,
                signature=signature,
                org_encryption_key=settings.org_encryption_key.get_secret_value(),
                webhook_allowlist=tuple(settings.workflow_webhook_allowlist or ()),
                trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
            )
            await session.commit()
        except SignatureError:
            # Missing/invalid/stale signature on a signed endpoint. One opaque 401
            # for every variant so a caller gets no oracle about which check failed.
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature"
            ) from None
        except Exception:
            await session.rollback()
            logger.exception("inbound webhook processing failed")
            raise
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown or disabled endpoint")
    return result
