"""Unit tests for the unhandled-exception handler.

Proves the fix for the "Network Error" class of bug: an unhandled 500 is caught
above CORSMiddleware, so the handler must re-attach the CORS headers itself or
the browser blocks the response and the frontend sees no status/message.
"""

from __future__ import annotations

import pytest
from api.exception_handlers import make_unhandled_exception_handler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

_ALLOWED = ["http://localhost:3000"]


def _app() -> FastAPI:
    """A minimal app wired exactly like create_app: CORS middleware plus the
    Exception handler, with a route that raises."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(Exception, make_unhandled_exception_handler(_ALLOWED))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    return app


def test_unhandled_500_carries_cors_headers_for_allowed_origin() -> None:
    # raise_server_exceptions=False so the handler runs instead of re-raising.
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Origin": "http://localhost:3000"})

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_unhandled_500_omits_cors_headers_for_disallowed_origin() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Origin": "http://evil.example"})

    assert resp.status_code == 500
    assert "access-control-allow-origin" not in resp.headers


def test_unhandled_500_without_origin_has_no_cors_headers() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.get("/boom")

    assert resp.status_code == 500
    assert "access-control-allow-origin" not in resp.headers
