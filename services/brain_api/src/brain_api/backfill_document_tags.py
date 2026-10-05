"""Backfill folder tags and masks onto fact-graph Document nodes.

Folder-limited graph search (``folder_tags`` on ``/api/vector-chat`` and
``/api/v1/ask``) tests the folder on a claim's source Document node. Nodes written
before they carried tags have none, so their claims are hidden from every
folder-limited caller (fails closed) until this copies each document's current
tags and masks over from the vector store. Idempotent; safe to re-run.

``--repair-empty-masks`` first rewrites chunk payloads a metadata update left with
``access_keys == []`` to the public ``[0]`` sentinel ingest uses (``[]`` matches no
mask-filtered search, so such public documents were invisible to restricted
readers). Idempotent too; it needs only the vector store, so it runs even when the
fact engine is off (the graph backfill is then skipped).

Documents with nothing to copy from are skipped and counted; the exit status is
non-zero only when a tenant FAILED, never for skips.

Run where brain-api's environment is configured, e.g.::

    docker exec km2_brain_api python -m brain_api.backfill_document_tags --tenant-id <org-uuid> [...]
    docker exec km2_brain_api python -m brain_api.backfill_document_tags --tenant-id <org-uuid> --repair-empty-masks
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from brain_api.config import BrainAPISettings
from brain_api.services.ingest_service import IngestService
from brain_api.stores import Stores

logger = logging.getLogger("brain_api.backfill_document_tags")


def _make_stores() -> Stores:
    return Stores(BrainAPISettings())  # type: ignore[call-arg]


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--tenant-id",
        action="append",
        required=True,
        help="Tenant (org id) to backfill; repeat for several.",
    )
    parser.add_argument(
        "--repair-empty-masks",
        action="store_true",
        help="Also rewrite chunks stored with access_keys == [] to the public [0] sentinel (idempotent).",
    )
    return parser.parse_args(argv)


def _run_tenant(stores: Stores, service: IngestService, tenant_id: str, *, repair: bool) -> dict[str, int]:
    """Repair (when asked) then backfill one tenant; returns its counts."""
    totals = {"documents": 0, "claims_recomputed": 0, "skipped": 0, "masks_repaired": 0}
    if repair:
        repaired = stores.vector.repair_empty_access_keys(tenant_id)
        totals["masks_repaired"] = repaired["repaired"]
        logger.info(
            "Tenant %s: %d of %d empty-mask chunks repaired", tenant_id, repaired["repaired"], repaired["scanned"]
        )
    if stores.settings.use_fact_engine:
        result = service.backfill_document_graph_metadata(tenant_id)
        for field in ("documents", "claims_recomputed", "skipped"):
            totals[field] = int(result.get(field, 0))
        logger.info(
            "Tenant %s: %d documents, %d claims re-masked, %d skipped",
            tenant_id,
            totals["documents"],
            totals["claims_recomputed"],
            totals["skipped"],
        )
    return totals


def main(argv: list[str] | None = None) -> int:
    """Backfill (and optionally repair) each ``--tenant-id``; exit 1 only if a
    tenant failed, or if there is nothing to do (fact engine off, no repair)."""
    args = _parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stores = _make_stores()
    try:
        if not stores.settings.use_fact_engine:
            if not args.repair_empty_masks:
                logger.error("USE_FACT_ENGINE is off; there are no fact-graph Document nodes to backfill")
                return 1
            logger.warning("USE_FACT_ENGINE is off: repairing masks only, skipping the graph backfill")
        service = IngestService(stores)
        totals = {"documents": 0, "claims_recomputed": 0, "skipped": 0, "masks_repaired": 0}
        failed = 0
        for tenant_id in args.tenant_id:
            try:
                counts = _run_tenant(stores, service, tenant_id, repair=args.repair_empty_masks)
            except Exception:
                failed += 1
                logger.exception("Backfill failed for tenant %s", tenant_id)
                continue
            for field, value in counts.items():
                totals[field] += value
        print(  # noqa: T201 - the operator's summary line
            "Totals: "
            + " ".join(f"{field}={value}" for field, value in totals.items())
            + f" tenants={len(args.tenant_id)} failed_tenants={failed}"
        )
        return 1 if failed else 0
    finally:
        asyncio.run(stores.close())


if __name__ == "__main__":
    raise SystemExit(main())
