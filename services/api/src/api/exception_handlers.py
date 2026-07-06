"""Application-wide exception handling.

An unhandled exception is caught by Starlette's ``ServerErrorMiddleware``, which
sits *above* ``CORSMiddleware`` in the stack — so the default 500 response
carries no ``Access-Control-Allow-Origin`` header. A browser then blocks the
response and the frontend sees a bare "Network Error" with no status or message
instead of a real 500. This handler produces the 500 itself and re-attaches the
CORS headers so a cross-origin caller can actually read the error.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def _cors_headers(request: Request, allow_origins: Sequence[str]) -> dict[str, str]:
    """Echo the CORS headers ``CORSMiddleware`` would have added.

    Mirrors the middleware's allow-list check so a 500 raised *above* that
    middleware is still readable by the browser. Credentials mode forbids a
    ``*`` origin, so the specific request ``Origin`` is always echoed back.
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}
    if "*" in allow_origins or origin in allow_origins:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


def make_unhandled_exception_handler(
    allow_origins: Sequence[str],
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Build the 500 handler bound to the configured CORS origins."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
            headers=_cors_headers(request, allow_origins),
        )

    return handler
