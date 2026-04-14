"""Service-to-service API key authentication."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from brain_api.config import BrainAPISettings

logger = logging.getLogger(__name__)
_api_key_header = APIKeyHeader(name="X-API-Key")

_settings: BrainAPISettings | None = None


def get_settings() -> BrainAPISettings:
    global _settings
    if _settings is None:
        _settings = BrainAPISettings()  # type: ignore[call-arg]
    return _settings


async def require_api_key(
    api_key: Annotated[str, Security(_api_key_header)],
    settings: Annotated[BrainAPISettings, Depends(get_settings)],
) -> str:
    """Validate the X-API-Key header against the configured secret."""
    if not settings.api_key:
        logger.warning("BRAIN_API_KEY not configured — rejecting all requests")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not configured",
        )

    if api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return api_key
