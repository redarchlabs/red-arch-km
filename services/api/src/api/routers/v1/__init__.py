"""Public, versioned enterprise API (``/api/v1``).

Every route here is authenticated by an org **API key** (not a Clerk session) and
rate-limited per client IP and per key. The routers are thin wrappers over the same
services the first-party UI uses — reusing the shared helpers in ``services/`` — so
the public contract can stay stable while the internals evolve.

``router`` aggregates the per-domain sub-routers and applies the rate limiters once
for the whole surface; each endpoint declares the single scope it requires.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth.api_key import enforce_api_rate_limit, enforce_ip_rate_limit
from api.routers.v1 import agents, config, entities, knowledge, records, reports, search, workflows

# Two limiters run for every v1 route, in order: the per-client-IP throttle first
# (pre-auth, so floods of missing/invalid keys can't hammer the key lookup), then
# the per-key quota (which resolves + caches the API-key principal). Individual
# endpoints add their scope + session dependencies.
router = APIRouter(dependencies=[Depends(enforce_ip_rate_limit), Depends(enforce_api_rate_limit)])

router.include_router(entities.router, prefix="/entities", tags=["v1: entities"])
router.include_router(records.router, prefix="/entities", tags=["v1: records"])
router.include_router(reports.router, prefix="/reports", tags=["v1: reports"])
router.include_router(workflows.router, prefix="/workflows", tags=["v1: workflows"])
router.include_router(search.router, prefix="/search", tags=["v1: search"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["v1: knowledge"])
router.include_router(agents.router, prefix="", tags=["v1: agents"])
router.include_router(config.router, prefix="/config", tags=["v1: config"])
