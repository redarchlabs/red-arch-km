"""Health check endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    # NOTE(REDARCH-12): readiness currently returns a static "ok". Wiring real
    # DB / Redis / brain-api connectivity probes is tracked separately and
    # intentionally deferred here to avoid changing health-probe behavior.
    return {"status": "ok"}
