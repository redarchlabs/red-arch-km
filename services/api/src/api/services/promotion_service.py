"""Release / promotion orchestration — the change-management control plane.

Ties the bundle engine (exporter/importer/diff) to the governance model
(:mod:`api.models.promotion`): freeze a selection into an immutable **release**,
move it through a draft → in_review → approved state machine with per-approver
audit, then **promote** it to a target (another org in this DB for Phase 2) with a
reverse snapshot captured for **rollback**.

Runs on a privileged/bypass session with every read/write scoped by an explicit
``org_id`` (the source org) — and, for the apply, the target org id — mirroring
the existing ``/api/migration`` import. Cross-org authorization (the caller is an
admin of BOTH orgs) is enforced by the router before this service is called.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.config import Settings
from api.models.promotion import (
    PromotionStatus,
    PromotionTarget,
    PromotionTargetKind,
    Release,
    ReleaseApproval,
    ReleaseItem,
    ReleasePromotion,
    ReleaseStatus,
)
from api.services.crypto import decrypt_secret, encrypt_secret
from api.services.migration.bundle import RESOURCE_ORDER, CollisionStrategy, Selection
from api.services.migration.diff import (
    DATA_RESOURCE_TYPES,
    build_lineage_index,
    object_fingerprint,
)
from api.services.migration.exporter import MigrationExporter
from api.services.migration.promotion import PromotionExecutor, PromotionResult
from api.services.migration.transport import OutboundPushClient, TransportError


class PromotionError(Exception):
    """A user-facing 400: bad state transition, missing object, invalid target."""


def _canonical_hash(resources: dict[str, Any]) -> str:
    payload = json.dumps(resources, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _redact_summary(result: PromotionResult) -> dict[str, Any]:
    """The stored audit blob: import counts + deleted + diff totals, with secret
    VALUES removed from generated_secrets (only kind/name survive)."""
    summary = result.import_summary
    data: dict[str, Any] = {"deleted": result.deleted, "diff_totals": result.diff.totals}
    if summary is not None:
        dumped = summary.model_dump()
        dumped["generated_secrets"] = [
            {"kind": s.get("kind"), "name": s.get("name")} for s in dumped.get("generated_secrets", [])
        ]
        data["import"] = dumped
    return data


class PromotionService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, settings: Settings) -> None:
        self._session = session
        self._org_id = org_id
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Targets
    # ------------------------------------------------------------------ #
    async def list_targets(self) -> list[PromotionTarget]:
        rows = await self._session.execute(
            select(PromotionTarget).where(PromotionTarget.org_id == self._org_id).order_by(PromotionTarget.name)
        )
        return list(rows.scalars().all())

    async def get_target(self, target_id: uuid.UUID) -> PromotionTarget | None:
        row = await self._session.execute(
            select(PromotionTarget).where(
                PromotionTarget.id == target_id, PromotionTarget.org_id == self._org_id
            )
        )
        return row.scalar_one_or_none()

    async def create_target(
        self,
        *,
        name: str,
        kind: PromotionTargetKind,
        target_org_id: uuid.UUID | None = None,
        base_url: str | None = None,
        remote_org_id: uuid.UUID | None = None,
        api_key: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> PromotionTarget:
        if kind is PromotionTargetKind.LOCAL_ORG:
            if target_org_id is None:
                raise PromotionError("a local-org target needs target_org_id")
            if target_org_id == self._org_id:
                raise PromotionError("a target cannot be the source org itself")
            base_url = remote_org_id = None  # keep the CHECK-constraint shape
        else:  # remote_instance
            if not base_url:
                raise PromotionError("a remote target needs base_url")
            target_org_id = None
        target = PromotionTarget(
            org_id=self._org_id,
            name=name,
            kind=kind.value,
            target_org_id=target_org_id,
            base_url=base_url,
            remote_org_id=remote_org_id,
            secret_encrypted=self._encrypt(api_key),
            config=config or {},
        )
        self._session.add(target)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise PromotionError(f"a target named {name!r} already exists") from exc
        return target

    async def delete_target(self, target_id: uuid.UUID) -> None:
        target = await self.get_target(target_id)
        if target is None:
            raise PromotionError("target not found")
        await self._session.delete(target)
        await self._session.flush()

    async def test_target(self, target: PromotionTarget) -> dict[str, Any]:
        """Probe a remote target (reachability + the key's config access). Returns an
        ok/error result rather than raising, so the UI can render either outcome."""
        if target.kind != PromotionTargetKind.REMOTE_INSTANCE.value:
            raise PromotionError("only remote-instance targets can be tested")
        try:
            info = await OutboundPushClient(self._settings).ping(
                target.base_url or "", self._remote_key(target)
            )
            return {"ok": True, "remote_bundle_format_version": info.get("bundle_format_version"), "error": None}
        except (TransportError, PromotionError, httpx.HTTPError) as exc:
            return {"ok": False, "remote_bundle_format_version": None, "error": str(exc)}

    def _encrypt(self, api_key: str | None) -> str | None:
        if not api_key:
            return None
        return encrypt_secret(api_key, self._settings.org_encryption_key.get_secret_value())

    # ------------------------------------------------------------------ #
    # Releases
    # ------------------------------------------------------------------ #
    async def list_releases(self) -> list[Release]:
        rows = await self._session.execute(
            select(Release).where(Release.org_id == self._org_id).order_by(Release.created_at.desc())
        )
        return list(rows.scalars().all())

    async def get_release(self, release_id: uuid.UUID) -> Release | None:
        row = await self._session.execute(
            select(Release).where(Release.id == release_id, Release.org_id == self._org_id)
        )
        return row.scalar_one_or_none()

    async def _release_or_raise(self, release_id: uuid.UUID) -> Release:
        release = await self.get_release(release_id)
        if release is None:
            raise PromotionError("release not found")
        return release

    async def create_release(
        self,
        *,
        name: str,
        description: str | None,
        selection: Selection | None,
        created_by_id: uuid.UUID | None,
    ) -> Release:
        """Freeze the selected config into an immutable release snapshot + inventory."""
        bundle = await MigrationExporter(self._session, self._org_id).export(
            selection=selection, include_records=False, include_documents=False
        )
        resources = bundle["resources"]
        release = Release(
            org_id=self._org_id,
            name=name,
            description=description,
            status=ReleaseStatus.DRAFT.value,
            bundle=bundle,
            bundle_hash=_canonical_hash(resources),
            bundle_format_version=bundle.get("format_version"),
            created_by_id=created_by_id,
        )
        self._session.add(release)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise PromotionError(f"a release named {name!r} already exists") from exc
        await self._populate_items(release, resources)
        await self._session.flush()
        return release

    async def _populate_items(self, release: Release, resources: dict[str, Any]) -> None:
        """Index every config object in the frozen bundle (lineage + fingerprint)."""
        lineage_index = build_lineage_index(resources)
        for rtype in RESOURCE_ORDER:
            if rtype in DATA_RESOURCE_TYPES:
                continue
            for obj in resources.get(rtype) or []:
                if not isinstance(obj, dict) or "lineage_id" not in obj:
                    continue
                self._session.add(
                    ReleaseItem(
                        org_id=self._org_id,
                        release_id=release.id,
                        object_type=rtype,
                        lineage_id=uuid.UUID(str(obj["lineage_id"])),
                        source_object_id=uuid.UUID(str(obj["id"])),
                        natural_key=str(obj.get("slug") or obj.get("name") or obj.get("title") or "")[:255],
                        fingerprint=object_fingerprint(obj, lineage_index),
                    )
                )

    async def list_items(self, release_id: uuid.UUID) -> list[ReleaseItem]:
        rows = await self._session.execute(
            select(ReleaseItem).where(
                ReleaseItem.release_id == release_id, ReleaseItem.org_id == self._org_id
            )
        )
        return list(rows.scalars().all())

    # --- state machine ------------------------------------------------- #
    @staticmethod
    def _require_status(release: Release, allowed: set[ReleaseStatus]) -> None:
        if release.status not in {s.value for s in allowed}:
            allowed_str = ", ".join(sorted(s.value for s in allowed))
            raise PromotionError(f"release is {release.status!r}; expected one of: {allowed_str}")

    async def submit_release(self, release_id: uuid.UUID) -> Release:
        release = await self._release_or_raise(release_id)
        self._require_status(release, {ReleaseStatus.DRAFT, ReleaseStatus.REJECTED})
        release.status = ReleaseStatus.IN_REVIEW.value
        release.submitted_at = func.now()
        await self._session.flush()
        return release

    async def _record_decision(
        self, release: Release, *, approver_id: uuid.UUID | None, decision: str, comment: str | None
    ) -> None:
        """Upsert this approver's decision. ``release_approvals`` is unique per
        (release, approver), and a release can legally cycle in_review → rejected →
        (resubmit) → in_review, so the same reviewer may decide more than once —
        update the prior row instead of inserting a duplicate (which would 500 on
        the unique constraint)."""
        existing = None
        if approver_id is not None:
            row = await self._session.execute(
                select(ReleaseApproval).where(
                    ReleaseApproval.release_id == release.id,
                    ReleaseApproval.org_id == self._org_id,
                    ReleaseApproval.approver_id == approver_id,
                )
            )
            existing = row.scalar_one_or_none()
        if existing is not None:
            existing.decision = decision
            existing.comment = comment
        else:
            self._session.add(
                ReleaseApproval(
                    org_id=self._org_id,
                    release_id=release.id,
                    approver_id=approver_id,
                    decision=decision,
                    comment=comment,
                )
            )

    async def approve_release(
        self, release_id: uuid.UUID, *, approver_id: uuid.UUID | None, comment: str | None
    ) -> Release:
        release = await self._release_or_raise(release_id)
        self._require_status(release, {ReleaseStatus.IN_REVIEW})
        await self._record_decision(release, approver_id=approver_id, decision="approved", comment=comment)
        release.status = ReleaseStatus.APPROVED.value
        release.approved_at = func.now()
        await self._session.flush()
        return release

    async def reject_release(
        self, release_id: uuid.UUID, *, approver_id: uuid.UUID | None, comment: str | None
    ) -> Release:
        release = await self._release_or_raise(release_id)
        self._require_status(release, {ReleaseStatus.IN_REVIEW})
        await self._record_decision(release, approver_id=approver_id, decision="rejected", comment=comment)
        release.status = ReleaseStatus.REJECTED.value
        await self._session.flush()
        return release

    async def list_approvals(self, release_id: uuid.UUID) -> list[ReleaseApproval]:
        rows = await self._session.execute(
            select(ReleaseApproval)
            .where(ReleaseApproval.release_id == release_id, ReleaseApproval.org_id == self._org_id)
            .order_by(ReleaseApproval.created_at)
        )
        return list(rows.scalars().all())

    # ------------------------------------------------------------------ #
    # Diff / Promote / Rollback
    # ------------------------------------------------------------------ #
    def _validate_target(self, target: PromotionTarget) -> None:
        if not target.enabled:
            raise PromotionError("target is disabled")
        if target.kind == PromotionTargetKind.LOCAL_ORG.value and target.target_org_id == self._org_id:
            raise PromotionError("cannot promote into the source org")

    def _remote_key(self, target: PromotionTarget) -> str:
        if not target.secret_encrypted:
            raise PromotionError("remote target has no API key; re-enter it")
        return decrypt_secret(target.secret_encrypted, self._settings.org_encryption_key.get_secret_value())

    async def _push_remote(
        self,
        target: PromotionTarget,
        bundle: dict[str, Any],
        *,
        strategy: CollisionStrategy,
        apply_deletes: bool,
        allow_data: bool,
        override_inflight: bool,
        dry_run: bool,
    ) -> PromotionResult:
        """Push a bundle to a remote KM2 instance's inbound receiver.

        The remote applies it and returns the result — including the reverse
        snapshot, which the source stores so a rollback can re-push it. A
        ``TransportError`` (SSRF/size/unreachable/remote error) surfaces as a 400;
        an in-flight block surfaces as ``PromotionBlocked`` (409)."""
        client = OutboundPushClient(self._settings)
        try:
            return await client.push(
                base_url=target.base_url or "",
                api_key=self._remote_key(target),
                bundle=bundle,
                strategy=strategy,
                apply_deletes=apply_deletes,
                allow_data=allow_data,
                override_inflight=override_inflight,
                dry_run=dry_run,
            )
        except TransportError as exc:
            raise PromotionError(str(exc)) from exc

    async def preview_promotion(
        self, release_id: uuid.UUID, target_id: uuid.UUID, *, strategy: CollisionStrategy, apply_deletes: bool
    ) -> PromotionResult:
        """Read-only diff + would-be summary + in-flight blockers. Rolls the session
        back so nothing persists."""
        release = await self._release_or_raise(release_id)
        target = await self.get_target(target_id)
        if target is None:
            raise PromotionError("target not found")
        self._validate_target(target)
        bundle = release.bundle or {}
        if target.kind == PromotionTargetKind.LOCAL_ORG.value:
            if target.target_org_id is None:
                raise PromotionError("local-org target is missing target_org_id")
            # Scope the read/dry-run to the TARGET org so RLS enforces the boundary
            # (defense in depth over the explicit org_id the executor passes).
            await db_scope.enter_tenant_owner(self._session, target.target_org_id)
            executor = PromotionExecutor(self._session, target.target_org_id, self._settings)
            result = await executor.preview(bundle, strategy=strategy, apply_deletes=apply_deletes)
            await self._session.rollback()  # nothing persists; also clears the tenant scope
            return result
        return await self._push_remote(
            target, bundle, strategy=strategy, apply_deletes=apply_deletes,
            allow_data=False, override_inflight=False, dry_run=True,
        )

    async def promote_release(
        self,
        release_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        strategy: CollisionStrategy,
        apply_deletes: bool,
        allow_data: bool,
        override_inflight: bool,
        promoted_by_id: uuid.UUID | None,
    ) -> tuple[ReleasePromotion, PromotionResult]:
        """Apply an APPROVED release to a target and record the promotion.

        Atomic: the apply, the reverse-snapshot capture, and the promotion record
        commit together. On an in-flight block nothing is persisted (the executor
        raises before mutating). Returns the record and the live result (whose
        generated_secrets are surfaced once and NOT persisted)."""
        # Lock the release row so two concurrent promotes/rollbacks of the same
        # release serialize (the status check + record insert are then atomic).
        release = await self._lock_release(release_id)
        self._require_status(release, {ReleaseStatus.APPROVED})
        target = await self.get_target(target_id)
        if target is None:
            raise PromotionError("target not found")
        self._validate_target(target)
        bundle = release.bundle or {}

        importer = None
        if target.kind == PromotionTargetKind.LOCAL_ORG.value:
            if target.target_org_id is None:
                raise PromotionError("local-org target is missing target_org_id")
            # Apply RLS-scoped to the TARGET org (defense in depth), then return to
            # cross-org scope to write the promotion record (owned by the source org).
            await db_scope.enter_tenant_owner(self._session, target.target_org_id)
            executor = PromotionExecutor(self._session, target.target_org_id, self._settings)
            result, importer = await executor.promote(
                bundle,
                strategy=strategy,
                apply_deletes=apply_deletes,
                allow_data=allow_data,
                override_inflight=override_inflight,
            )
            await db_scope.exit_to_bypass(self._session)
            record_target_org = target.target_org_id
        else:
            result = await self._push_remote(
                target, bundle, strategy=strategy, apply_deletes=apply_deletes,
                allow_data=allow_data, override_inflight=override_inflight, dry_run=False,
            )
            record_target_org = target.remote_org_id

        promotion = ReleasePromotion(
            org_id=self._org_id,
            release_id=release.id,
            target_id=target.id,
            target_kind=target.kind,
            target_label=target.name,
            target_org_id=record_target_org,
            status=PromotionStatus.PROMOTED.value,
            strategy=strategy.value,
            dry_run=False,
            promoted_by_id=promoted_by_id,
            result_summary=_redact_summary(result),
            pre_state_bundle=result.reverse_snapshot,
            pre_state_hash=_canonical_hash((result.reverse_snapshot or {}).get("resources", {})),
            started_at=func.now(),
            finished_at=func.now(),
        )
        self._session.add(promotion)
        await self._session.commit()
        if importer is not None:
            # The commit cleared the transaction-scoped RLS GUCs; re-enter privileged
            # scope so the post-commit ingest enqueue (doc.celery_task_id writes) is
            # not blocked by RLS failing closed.
            await db_scope.enter_bypass(self._session)
            await importer.dispatch_pending_ingests(result.import_summary)
        return promotion, result

    async def rollback_promotion(
        self, promotion_id: uuid.UUID, *, rolled_back_by_id: uuid.UUID | None
    ) -> tuple[ReleasePromotion, PromotionResult]:
        """Undo a promoted promotion by re-applying its reverse snapshot."""
        # Lock the promotion row so two concurrent rollbacks (or a rollback racing a
        # re-promote) cannot both pass the status check and double-apply.
        promotion = await self._lock_promotion(promotion_id)
        if promotion is None:
            raise PromotionError("promotion not found")
        if promotion.status != PromotionStatus.PROMOTED.value:
            raise PromotionError(f"promotion is {promotion.status!r}; only a promoted one can be rolled back")
        if not promotion.pre_state_bundle:
            raise PromotionError("no reverse snapshot was captured; cannot roll back")

        importer = None
        if promotion.target_kind == PromotionTargetKind.LOCAL_ORG.value:
            if promotion.target_org_id is None:
                raise PromotionError("local-org promotion is missing target_org_id")
            await db_scope.enter_tenant_owner(self._session, promotion.target_org_id)
            executor = PromotionExecutor(self._session, promotion.target_org_id, self._settings)
            result, importer = await executor.rollback(promotion.pre_state_bundle)
            await db_scope.exit_to_bypass(self._session)
        else:
            target = await self.get_target(promotion.target_id) if promotion.target_id else None
            if target is None:
                raise PromotionError("the remote target was removed; cannot roll back")
            # Rollback == re-push the reverse snapshot with deletes on (restore
            # overwrites + remove what the promotion created), overriding in-flight.
            result = await self._push_remote(
                target, promotion.pre_state_bundle, strategy=CollisionStrategy.OVERWRITE,
                apply_deletes=True, allow_data=False, override_inflight=True, dry_run=False,
            )

        promotion.status = PromotionStatus.ROLLED_BACK.value
        promotion.rolled_back_at = func.now()
        promotion.rolled_back_by_id = rolled_back_by_id
        inverse = ReleasePromotion(
            org_id=self._org_id,
            release_id=promotion.release_id,
            target_id=promotion.target_id,
            target_kind=promotion.target_kind,
            target_label=promotion.target_label,
            target_org_id=promotion.target_org_id,
            status=PromotionStatus.PROMOTED.value,
            strategy=CollisionStrategy.OVERWRITE.value,
            dry_run=False,
            promoted_by_id=rolled_back_by_id,
            result_summary=_redact_summary(result),
            rollback_source_id=promotion.id,
            started_at=func.now(),
            finished_at=func.now(),
        )
        self._session.add(inverse)
        await self._session.commit()
        if importer is not None:
            await db_scope.enter_bypass(self._session)
            await importer.dispatch_pending_ingests(result.import_summary)
        return promotion, result

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #
    async def _lock_release(self, release_id: uuid.UUID) -> Release:
        row = await self._session.execute(
            select(Release)
            .where(Release.id == release_id, Release.org_id == self._org_id)
            .with_for_update()
        )
        release = row.scalar_one_or_none()
        if release is None:
            raise PromotionError("release not found")
        return release

    async def _lock_promotion(self, promotion_id: uuid.UUID) -> ReleasePromotion | None:
        row = await self._session.execute(
            select(ReleasePromotion)
            .where(ReleasePromotion.id == promotion_id, ReleasePromotion.org_id == self._org_id)
            .with_for_update()
        )
        return row.scalar_one_or_none()

    async def get_promotion(self, promotion_id: uuid.UUID) -> ReleasePromotion | None:
        row = await self._session.execute(
            select(ReleasePromotion).where(
                ReleasePromotion.id == promotion_id, ReleasePromotion.org_id == self._org_id
            )
        )
        return row.scalar_one_or_none()

    async def list_promotions(self, *, release_id: uuid.UUID | None = None) -> list[ReleasePromotion]:
        stmt = select(ReleasePromotion).where(ReleasePromotion.org_id == self._org_id)
        if release_id is not None:
            stmt = stmt.where(ReleasePromotion.release_id == release_id)
        rows = await self._session.execute(stmt.order_by(ReleasePromotion.created_at.desc()))
        return list(rows.scalars().all())
