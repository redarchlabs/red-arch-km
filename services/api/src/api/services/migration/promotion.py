"""Promotion executor: apply a release bundle to a target org, with preview and
rollback.

Wraps the existing exporter/importer/diff so a promotion:

* PREVIEWS by exporting the target's current state, diffing, and running the
  importer in dry-run (rolled back) — nothing persists.
* PROMOTES by first capturing a **reverse snapshot** of the target (so it can be
  undone), then applying the bundle lineage-aware, then optionally running guarded
  deletes.
* ROLLS BACK by re-applying that reverse snapshot with deletes on — restoring
  overwritten content and removing whatever the promotion created.

The transactional boundary mirrors the import route: this class does all DB work
but never commits. The caller commits once, then calls
``importer.dispatch_pending_ingests`` so document ingestion is enqueued only after
the rows are visible to the worker.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.services.migration.bundle import RESOURCE_ORDER, CollisionStrategy, ImportSummary
from api.services.migration.deleter import MigrationDeleter
from api.services.migration.diff import (
    DATA_RESOURCE_TYPES,
    BundleDiff,
    ObjectStatus,
    compute_diff,
)
from api.services.migration.exporter import MigrationExporter
from api.services.migration.importer import MigrationImporter
from api.services.migration.inflight import InFlightBlocker, InFlightGuard


def _managed_delete_types(source_resources: dict[str, Any]) -> frozenset[str]:
    """Which config types a release may delete on the target: only the ones the
    release actually contains. A forms-only release must never mark the target's
    workflows or entities (absent from the release) as deletions."""
    return frozenset(
        rtype
        for rtype in RESOURCE_ORDER
        if rtype not in DATA_RESOURCE_TYPES and source_resources.get(rtype)
    )


class PromotionBlocked(Exception):
    """Raised when a destructive promotion is blocked by in-flight workflow runs
    and the caller did not override."""

    def __init__(self, blockers: list[InFlightBlocker]) -> None:
        self.blockers = blockers
        names = ", ".join(f"{b.resource_type}:{b.name}" for b in blockers)
        super().__init__(f"blocked by in-flight workflow runs on: {names}")


class PromotionResult(BaseModel):
    diff: BundleDiff
    import_summary: ImportSummary | None = None
    deleted: dict[str, int] = Field(default_factory=dict)
    reverse_snapshot: dict[str, Any] | None = None
    blockers: list[InFlightBlocker] = Field(default_factory=list)
    dry_run: bool = False


def deletion_set(diff: BundleDiff) -> dict[str, set[str]]:
    """The ``{resource_type: {lineage_id}}`` a promotion would delete — the diff's
    DELETED objects that carry a durable lineage (no lineage → never auto-deleted)."""
    out: dict[str, set[str]] = {}
    for rd in diff.resources:
        for obj in rd.objects:
            if obj.status is ObjectStatus.DELETED and obj.lineage_id:
                out.setdefault(rd.resource_type, set()).add(obj.lineage_id)
    return out


class PromotionExecutor:
    def __init__(self, session: AsyncSession, target_org_id: uuid.UUID, settings: Settings) -> None:
        self._session = session
        self._org_id = target_org_id
        self._settings = settings

    async def _target_config(self) -> dict[str, Any]:
        """Current config-only export of the target org (the diff/rollback baseline)."""
        bundle = await MigrationExporter(self._session, self._org_id).export(
            include_records=False, include_documents=False
        )
        return bundle["resources"]

    async def preview(
        self,
        bundle: dict[str, Any],
        *,
        strategy: CollisionStrategy = CollisionStrategy.SKIP,
        apply_deletes: bool = False,
    ) -> PromotionResult:
        """Dry-run: diff + would-be import summary + in-flight blockers. The caller
        MUST roll the session back — the importer's dry-run leaves nothing, but the
        target export/diff read on the same session."""
        source = bundle.get("resources") or {}
        target = await self._target_config()
        diff = compute_diff(source, target, manage_deletes_for=_managed_delete_types(source))
        summary = await MigrationImporter(self._session, self._org_id, self._settings).import_bundle(
            bundle, strategy, dry_run=True
        )
        blockers: list[InFlightBlocker] = []
        if apply_deletes and diff.has_deletes:
            blockers = await InFlightGuard(self._session, self._org_id).check(deletion_set(diff))
        return PromotionResult(
            diff=diff, import_summary=summary, blockers=blockers, dry_run=True
        )

    async def promote(
        self,
        bundle: dict[str, Any],
        *,
        strategy: CollisionStrategy = CollisionStrategy.SKIP,
        apply_deletes: bool = False,
        allow_data: bool = False,
        override_inflight: bool = False,
        capture_rollback: bool = True,
        full_delete_scope: bool = False,
    ) -> tuple[PromotionResult, MigrationImporter]:
        """Apply ``bundle`` to the target. Does NOT commit — the caller commits, then
        calls ``importer.dispatch_pending_ingests(result.import_summary)``.

        Raises :class:`PromotionBlocked` if ``apply_deletes`` would remove an object
        with in-flight runs and ``override_inflight`` is not set.

        ``full_delete_scope`` widens deletion management to every config type (used by
        rollback, which restores the target to a captured full snapshot). A normal
        forward promote instead limits deletes to the types the release carries, so a
        partial release never marks unrelated types for deletion.
        """
        source = bundle.get("resources") or {}
        # Capture the reverse snapshot BEFORE any mutation, so rollback can restore.
        reverse_snapshot = None
        if capture_rollback:
            reverse_snapshot = await MigrationExporter(self._session, self._org_id).export(
                include_records=False, include_documents=False
            )

        target = await self._target_config()
        manage = None if full_delete_scope else _managed_delete_types(source)
        diff = compute_diff(source, target, manage_deletes_for=manage)
        deletions = deletion_set(diff) if apply_deletes else {}

        # In-flight guard: block destructive deletes under live runs unless overridden.
        blockers: list[InFlightBlocker] = []
        if deletions:
            blockers = await InFlightGuard(self._session, self._org_id).check(deletions)
            if blockers and not override_inflight:
                raise PromotionBlocked(blockers)

        importer = MigrationImporter(self._session, self._org_id, self._settings)
        summary = await importer.import_bundle(bundle, strategy, dry_run=False)

        deleted: dict[str, int] = {}
        if deletions:
            deleted = await MigrationDeleter(self._session, self._org_id, self._settings).delete_lineages(
                deletions, summary, allow_data=allow_data
            )

        result = PromotionResult(
            diff=diff,
            import_summary=summary,
            deleted=deleted,
            reverse_snapshot=reverse_snapshot,
            blockers=blockers,
        )
        return result, importer

    async def rollback(
        self, reverse_snapshot: dict[str, Any]
    ) -> tuple[PromotionResult, MigrationImporter]:
        """Undo a promotion by re-applying its reverse snapshot with deletes on:
        OVERWRITE restores changed objects, and objects created since (absent from
        the snapshot) are removed across every config type.

        The snapshot is a full ``km2-migration-bundle`` captured at promote time, so
        restoring it necessarily removes what the promotion created — including
        entities (and, with them, their data). Rollback therefore uses the full
        delete scope and ``allow_data``; it is a deliberate, operator-confirmed
        corrective action and overrides the in-flight guard.
        """
        return await self.promote(
            reverse_snapshot,
            strategy=CollisionStrategy.OVERWRITE,
            apply_deletes=True,
            allow_data=True,
            override_inflight=True,
            capture_rollback=False,
            full_delete_scope=True,
        )
