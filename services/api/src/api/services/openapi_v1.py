"""Public OpenAPI docs for the ``/api/v1`` enterprise surface only.

The app's own ``/docs`` is gated to ``debug`` and would expose every internal
route. Enterprise API consumers need stable, always-available docs for just the
public surface, so this module builds an OpenAPI document from the ``/api/v1``
routes alone (with an API-key security scheme) and serves it plus a Swagger UI at
``/api/v1/openapi.json`` and ``/api/v1/docs`` — gated by ``api_docs_enabled``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from api.config import get_settings

_V1_PREFIX = "/api/v1"

# Security schemes offered to API consumers: an Authorization: Bearer km2_... token
# (primary) and the X-API-Key header (alternative). Either satisfies a request.
_SECURITY_SCHEMES: dict[str, Any] = {
    "BearerApiKey": {
        "type": "http",
        "scheme": "bearer",
        "description": "Send your key as `Authorization: Bearer km2_...`",
    },
    "ApiKeyHeader": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Send your key as `X-API-Key: km2_...`",
    },
}


def build_v1_openapi(app: FastAPI) -> dict[str, Any]:
    """Build (and cache) an OpenAPI doc covering only the ``/api/v1`` routes."""
    cached: dict[str, Any] | None = getattr(app.state, "_v1_openapi", None)
    if cached is not None:
        return cached

    routes = [r for r in app.routes if getattr(r, "path", "").startswith(_V1_PREFIX)]
    schema = get_openapi(
        title="Red Arch Enterprise API",
        version="1.0.0",
        description=(
            "Programmatic REST access to entities, records, reports, workflows, "
            "search, and the knowledge base. Authenticate with an organization API "
            "key created in the Admin Area (send it as `Authorization: Bearer km2_...` "
            "or the `X-API-Key` header)."
        ),
        routes=routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = _SECURITY_SCHEMES
    # Apply the schemes globally so the Swagger "Authorize" dialog covers every op.
    schema["security"] = [{"BearerApiKey": []}, {"ApiKeyHeader": []}]
    app.state._v1_openapi = schema
    return schema


def register_v1_docs(app: FastAPI) -> None:
    """Attach the ``/api/v1/openapi.json`` + ``/api/v1/docs`` routes to ``app``."""

    def _guard() -> None:
        if not get_settings().api_docs_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API docs are disabled")

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    async def v1_openapi() -> JSONResponse:
        _guard()
        return JSONResponse(build_v1_openapi(app))

    @app.get("/api/v1/docs", include_in_schema=False)
    async def v1_docs() -> HTMLResponse:
        _guard()
        return get_swagger_ui_html(
            openapi_url="/api/v1/openapi.json",
            title="Red Arch Enterprise API — Docs",
        )
