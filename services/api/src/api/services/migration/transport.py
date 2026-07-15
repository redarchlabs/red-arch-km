"""Outbound transport for cross-instance promotion.

Pushes a frozen release bundle to another KM2 deployment's inbound receiver
(``POST /api/v1/config/promotions``), authenticated with that instance's org API
key. Every push is SSRF-guarded with the same deny-by-default allow-list the
workflow webhook actions use, requires HTTPS (except for explicitly trusted local
hosts), and is size-capped like the import route.

Local-org promotion does NOT go through here — it runs the executor in-process
(see ``promotion_service``). This module is only the remote leg.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from api.config import Settings
from api.services.migration.bundle import MAX_BUNDLE_BYTES, CollisionStrategy
from api.services.migration.inflight import InFlightBlocker
from api.services.migration.promotion import PromotionBlocked, PromotionResult
from api.services.workflow.actions import ActionError, assert_outbound_host_allowed

_PUSH_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class TransportError(Exception):
    """A remote push failed (SSRF-blocked, unreachable, auth, or a remote error)."""


class OutboundPushClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _guard(self, base_url: str) -> tuple[str, str]:
        parsed = urlsplit(base_url)
        host, scheme = parsed.hostname or "", parsed.scheme
        trusted = host in self._settings.workflow_trusted_local_hosts
        # Reuse the shared deny-by-default SSRF guard (allow-list + private-IP block).
        try:
            assert_outbound_host_allowed(
                host,
                scheme,
                webhook_allowlist=self._settings.workflow_webhook_allowlist,
                trusted_local_hosts=self._settings.workflow_trusted_local_hosts,
                action="promotion_push",
            )
        except ActionError as exc:
            raise TransportError(str(exc)) from exc
        # Cross-instance promotion carries an API key + whole config — require TLS
        # unless this is an explicitly trusted local bridge.
        if scheme != "https" and not trusted:
            raise TransportError("remote promotion requires an https base_url")
        return host, scheme

    async def ping(self, base_url: str, api_key: str) -> dict[str, object]:
        """Test-connection probe: validates reachability + the key's config access."""
        self._guard(base_url)
        url = f"{base_url.rstrip('/')}/api/v1/config/ping"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        if resp.status_code == 401 or resp.status_code == 403:
            raise TransportError("the remote rejected the API key (missing config access)")
        resp.raise_for_status()
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise TransportError("the remote returned a non-JSON response") from exc
        if not isinstance(body, dict):
            raise TransportError("the remote returned an unexpected response shape")
        return body

    async def push(
        self,
        *,
        base_url: str,
        api_key: str,
        bundle: dict,
        strategy: CollisionStrategy,
        apply_deletes: bool = False,
        allow_data: bool = False,
        dry_run: bool = False,
        override_inflight: bool = False,
        idempotency_key: str | None = None,
    ) -> PromotionResult:
        """POST the bundle to the remote receiver and return its result.

        Raises :class:`PromotionBlocked` on a 409 (in-flight runs on the target) and
        :class:`TransportError` for SSRF/size/transport/remote failures."""
        self._guard(base_url)
        payload = json.dumps(bundle).encode("utf-8")
        if len(payload) > MAX_BUNDLE_BYTES:
            raise TransportError(f"bundle exceeds the {MAX_BUNDLE_BYTES // (1024 * 1024)} MB limit")

        url = f"{base_url.rstrip('/')}/api/v1/config/promotions"
        headers = {"Authorization": f"Bearer {api_key}"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        params = {
            "strategy": strategy.value,
            "dry_run": str(dry_run).lower(),
            "apply_deletes": str(apply_deletes).lower(),
            "allow_data": str(allow_data).lower(),
            "override_inflight": str(override_inflight).lower(),
        }
        try:
            async with httpx.AsyncClient(timeout=_PUSH_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    params=params,
                    headers=headers,
                    files={"file": ("bundle.json", payload, "application/json")},
                )
        except httpx.HTTPError as exc:
            raise TransportError(f"could not reach the remote instance: {exc}") from exc

        if resp.status_code == 409:
            detail = _detail(resp)
            blockers = [InFlightBlocker.model_validate(b) for b in detail.get("blockers", [])]
            raise PromotionBlocked(blockers)
        if resp.status_code in (401, 403):
            raise TransportError("the remote rejected the API key (missing config:write)")
        if resp.status_code >= 400:
            raise TransportError(f"remote promotion failed ({resp.status_code}): {_detail(resp)}")
        # Guard against a hostile/misbehaving remote returning an unbounded body.
        if len(resp.content) > MAX_BUNDLE_BYTES:
            raise TransportError(f"remote response exceeds the {MAX_BUNDLE_BYTES // (1024 * 1024)} MB limit")
        try:
            return PromotionResult.model_validate(resp.json())
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise TransportError(f"the remote returned an invalid promotion result: {exc}") from exc


def _detail(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        return {"message": resp.text[:500]}
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, dict) else {"message": str(detail or body)}
