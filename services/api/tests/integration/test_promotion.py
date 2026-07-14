"""Integration tests for the release/promotion control plane (Phase 2).

Exercises the whole vertical against a real DB: freeze a release, move it through
the governance state machine, promote it into another org, and roll it back — plus
the guardrails (promote-before-approved, self-promotion).
"""

from __future__ import annotations

import pytest
from api.models.promotion import PromotionTargetKind
from api.repositories.custom_entity import EntityDefinitionRepository
from api.services.migration.bundle import CollisionStrategy
from api.services.promotion_service import PromotionError, PromotionService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant
from .test_migration_roundtrip import _make_org, _seed_source, _settings

pytestmark = pytest.mark.integration


async def _approved_release(svc: PromotionService, admin_session: AsyncSession):
    release = await svc.create_release(name="Release 1", description="first", selection=None, created_by_id=None)
    await admin_session.commit()
    await svc.submit_release(release.id)
    await admin_session.commit()
    await svc.approve_release(release.id, approver_id=None, comment="lgtm")
    await admin_session.commit()
    return release


@pytest.mark.asyncio
async def test_release_lifecycle_promote_and_rollback(admin_session: AsyncSession) -> None:
    source = await _make_org(admin_session, "Promo Source")
    target = await _make_org(admin_session, "Promo Target")
    await _seed_source(admin_session, source)

    await set_tenant(admin_session, str(source.id))
    svc = PromotionService(admin_session, source.id, _settings())

    # Register a local-org target + freeze a release.
    tgt = await svc.create_target(
        name="Staging", kind=PromotionTargetKind.LOCAL_ORG, target_org_id=target.id
    )
    release = await svc.create_release(name="R1", description=None, selection=None, created_by_id=None)
    await admin_session.commit()
    assert release.status == "draft"
    assert release.bundle_hash and release.bundle_format_version == 2
    items = await svc.list_items(release.id)
    assert {i.object_type for i in items} >= {"entities", "forms", "workflows"}

    # Governance: draft -> in_review -> approved (+ an approval row).
    await svc.submit_release(release.id)
    await admin_session.commit()
    await svc.approve_release(release.id, approver_id=None, comment="ship it")
    await admin_session.commit()
    assert (await svc.get_release(release.id)).status == "approved"
    assert len(await svc.list_approvals(release.id)) == 1

    # Promote into the (empty) target org.
    promotion, result = await svc.promote_release(
        release.id,
        tgt.id,
        strategy=CollisionStrategy.SKIP,
        apply_deletes=False,
        allow_data=False,
        override_inflight=False,
        promoted_by_id=None,
    )
    assert promotion.status == "promoted"
    assert promotion.pre_state_bundle is not None  # reverse snapshot captured
    tgt_defs = EntityDefinitionRepository(admin_session, target.id)
    assert await tgt_defs.get_by_slug("company") is not None
    assert await tgt_defs.get_by_slug("contact") is not None

    # Rollback: the target was empty before, so everything created is removed.
    await svc.rollback_promotion(promotion.id, rolled_back_by_id=None)
    assert await tgt_defs.get_by_slug("company") is None
    assert await tgt_defs.get_by_slug("contact") is None
    assert (await svc.get_promotion(promotion.id)).status == "rolled_back"
    # Original + the inverse (rollback) record.
    promos = await svc.list_promotions(release_id=release.id)
    assert len(promos) == 2
    assert any(p.rollback_source_id == promotion.id for p in promos)


@pytest.mark.asyncio
async def test_promote_requires_approved_release(admin_session: AsyncSession) -> None:
    source = await _make_org(admin_session, "Guard Source")
    target = await _make_org(admin_session, "Guard Target")
    await _seed_source(admin_session, source)
    await set_tenant(admin_session, str(source.id))
    svc = PromotionService(admin_session, source.id, _settings())

    tgt = await svc.create_target(name="Prod", kind=PromotionTargetKind.LOCAL_ORG, target_org_id=target.id)
    release = await svc.create_release(name="Draft R", description=None, selection=None, created_by_id=None)
    await admin_session.commit()

    # Still a draft → promotion is refused.
    with pytest.raises(PromotionError):
        await svc.promote_release(
            release.id,
            tgt.id,
            strategy=CollisionStrategy.SKIP,
            apply_deletes=False,
            allow_data=False,
            override_inflight=False,
            promoted_by_id=None,
        )


@pytest.mark.asyncio
async def test_target_cannot_be_source_org(admin_session: AsyncSession) -> None:
    source = await _make_org(admin_session, "Self Target Org")
    await set_tenant(admin_session, str(source.id))
    svc = PromotionService(admin_session, source.id, _settings())
    with pytest.raises(PromotionError):
        await svc.create_target(
            name="Self", kind=PromotionTargetKind.LOCAL_ORG, target_org_id=source.id
        )


@pytest.mark.asyncio
async def test_promote_then_change_and_repromote_is_idempotent_by_lineage(admin_session: AsyncSession) -> None:
    """A second promotion of an updated release overwrites the same target rows
    (matched by lineage) instead of duplicating them."""
    source = await _make_org(admin_session, "Idem Source")
    target = await _make_org(admin_session, "Idem Target")
    await _seed_source(admin_session, source)
    await set_tenant(admin_session, str(source.id))
    svc = PromotionService(admin_session, source.id, _settings())
    tgt = await svc.create_target(name="Env", kind=PromotionTargetKind.LOCAL_ORG, target_org_id=target.id)

    r1 = await _approved_release(svc, admin_session)
    await svc.promote_release(
        r1.id, tgt.id, strategy=CollisionStrategy.SKIP, apply_deletes=False,
        allow_data=False, override_inflight=False, promoted_by_id=None,
    )
    tgt_defs = EntityDefinitionRepository(admin_session, target.id)
    count_after_first = len((await tgt_defs.list_all(limit=100))[0])

    # A second release (fresh snapshot) promoted with OVERWRITE must not duplicate.
    r2 = await svc.create_release(name="R2", description=None, selection=None, created_by_id=None)
    await admin_session.commit()
    await svc.submit_release(r2.id)
    await admin_session.commit()
    await svc.approve_release(r2.id, approver_id=None, comment="again")
    await admin_session.commit()
    await svc.promote_release(
        r2.id, tgt.id, strategy=CollisionStrategy.OVERWRITE, apply_deletes=False,
        allow_data=False, override_inflight=False, promoted_by_id=None,
    )
    count_after_second = len((await tgt_defs.list_all(limit=100))[0])
    assert count_after_second == count_after_first  # lineage match → no duplicates
