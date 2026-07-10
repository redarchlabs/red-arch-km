"""Integration tests for the reporting engine + server-side record filtering.

Exercises the real Postgres path (runtime entity table, RLS-scoped repo): typed
server-side filters and keyset pagination, GROUP BY / metric aggregation with
date buckets + HAVING + ORDER BY, saved-report CRUD + run, and the report's
inclusion in the org import/export bundle with entity-id remapping.

Faithful to production wiring for the DDL/aggregate paths, everything runs on the
privileged ``admin_session`` (mirroring ``get_db``); repositories scope by
``org_id`` explicitly, so isolation holds even though the superuser bypasses RLS.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from api.models.org import Org
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.schemas.aggregate import AggregateQuery, GroupBy, HavingSpec, Metric, OrderSpec
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.schemas.report import ReportCreate, Visualization
from api.services.entity_service import EntityService
from api.services.migration import CollisionStrategy, MigrationExporter, MigrationImporter
from api.services.report_service import ReportService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

os.environ.setdefault("API_SECRET_KEY", "test-secret")

# (stage, amount) — 3 won (600), 1 lost (50), 2 open (100).
_DEALS = [("won", 100), ("won", 200), ("lost", 50), ("open", 75), ("open", 25), ("won", 300)]


def _settings():  # type: ignore[no-untyped-def]
    from api.config import get_settings

    return get_settings()


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def _load_repo(session: AsyncSession, org_id: uuid.UUID, definition_id: uuid.UUID) -> DynamicEntityRepository:
    definition = await EntityDefinitionRepository(session, org_id).get(definition_id)
    assert definition is not None
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition_id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition_id)
    return DynamicEntityRepository(session, org_id, definition, fields, rels)


async def _seed_deals(admin_session: AsyncSession, org: Org):  # type: ignore[no-untyped-def]
    await set_tenant(admin_session, str(org.id))
    svc = EntityService(admin_session, org.id)
    deal = await svc.create_definition(
        EntityDefinitionCreate(
            name="Deal",
            slug="deal",
            fields=[
                EntityFieldCreate(
                    name="Stage", slug="stage", field_type="picklist", picklist_options=["won", "lost", "open"]
                ),
                EntityFieldCreate(name="Amount", slug="amount", field_type="numeric"),
            ],
        )
    )
    await admin_session.commit()
    repo = await _load_repo(admin_session, org.id, deal.id)
    for stage, amount in _DEALS:
        await repo.create({"stage": stage, "amount": amount})
    await admin_session.commit()
    return deal, repo


class TestServerSideFiltering:
    async def test_eq_filter(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "rep-eq")
        _deal, repo = await _seed_deals(admin_session, org)
        items, _ = await repo.list(filters=[("stage", "eq", "won")], limit=50)
        assert len(items) == 3
        assert all(r["stage"] == "won" for r in items)

    async def test_range_and_in_filters(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "rep-range")
        _deal, repo = await _seed_deals(admin_session, org)
        gte, _ = await repo.list(filters=[("amount", "gte", 100)], limit=50)
        assert len(gte) == 3  # 100, 200, 300
        in_, _ = await repo.list(filters=[("stage", "in", ["won", "open"])], limit=50)
        assert len(in_) == 5

    async def test_isnull_false_matches_all(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "rep-null")
        _deal, repo = await _seed_deals(admin_session, org)
        items, _ = await repo.list(filters=[("amount", "isnull", False)], limit=50)
        assert len(items) == 6

    async def test_keyset_pagination_over_filtered_list(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "rep-page")
        _deal, repo = await _seed_deals(admin_session, org)
        # Walk a filtered result set two-at-a-time; the composite cursor must
        # continue correctly and collect exactly the 3 "won" rows once each.
        seen: set[str] = set()
        cursor = None
        pages = 0
        while True:
            items, cursor = await repo.list(filters=[("stage", "eq", "won")], limit=2, cursor=cursor)
            pages += 1
            for r in items:
                seen.add(str(r["id"]))
            if cursor is None:
                break
            assert pages < 10  # guard against a non-terminating cursor
        assert len(seen) == 3
        assert pages == 2


class TestAggregation:
    async def test_group_by_stage_count_and_sum(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "agg-group")
        _deal, repo = await _seed_deals(admin_session, org)
        res = await repo.aggregate(
            AggregateQuery(
                group_by=[GroupBy(field="stage")],
                metrics=[Metric(op="count"), Metric(op="sum", field="amount", alias="total")],
                order_by=[OrderSpec(key="stage", dir="asc")],
            )
        )
        by_stage = {row["stage"]: row for row in res.rows}
        assert by_stage["won"]["count"] == 3
        assert Decimal(by_stage["won"]["total"]) == Decimal(600)
        assert by_stage["lost"]["count"] == 1
        assert Decimal(by_stage["open"]["total"]) == Decimal(100)

    async def test_having_filters_aggregate_rows(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "agg-having")
        _deal, repo = await _seed_deals(admin_session, org)
        res = await repo.aggregate(
            AggregateQuery(
                group_by=[GroupBy(field="stage")],
                metrics=[Metric(op="sum", field="amount", alias="total")],
                having=[HavingSpec(metric="total", op="gt", value=100)],
            )
        )
        # only "won" (600) exceeds 100; lost=50, open=100 are excluded.
        assert {row["stage"] for row in res.rows} == {"won"}

    async def test_date_bucket_groups_all_into_one_month(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "agg-bucket")
        _deal, repo = await _seed_deals(admin_session, org)
        res = await repo.aggregate(
            AggregateQuery(group_by=[GroupBy(field="created_at", bucket="month")], metrics=[Metric(op="count")])
        )
        assert len(res.rows) == 1
        assert res.rows[0]["count"] == 6

    async def test_avg_min_max(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "agg-stats")
        _deal, repo = await _seed_deals(admin_session, org)
        res = await repo.aggregate(
            AggregateQuery(
                metrics=[
                    Metric(op="avg", field="amount", alias="a"),
                    Metric(op="min", field="amount", alias="lo"),
                    Metric(op="max", field="amount", alias="hi"),
                ]
            )
        )
        row = res.rows[0]
        assert Decimal(row["lo"]) == Decimal(25)
        assert Decimal(row["hi"]) == Decimal(300)


class TestSavedReports:
    async def test_create_run_report(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "rep-crud")
        deal, _repo = await _seed_deals(admin_session, org)
        svc = ReportService(admin_session, org.id)
        report = await svc.create_report(
            ReportCreate(
                name="Pipeline by stage",
                slug="pipeline_by_stage",
                entity_definition_id=deal.id,
                query=AggregateQuery(
                    group_by=[GroupBy(field="stage")],
                    metrics=[Metric(op="sum", field="amount", alias="total")],
                ),
                viz=Visualization(type="bar", x="stage", series=["total"]),
            )
        )
        await admin_session.commit()
        result = await svc.run_report(report.id)
        by_stage = {row["stage"]: Decimal(row["total"]) for row in result.rows}
        assert by_stage == {"won": Decimal(600), "lost": Decimal(50), "open": Decimal(100)}

    async def test_invalid_query_rejected_at_save(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "rep-bad")
        deal, _repo = await _seed_deals(admin_session, org)
        svc = ReportService(admin_session, org.id)
        from api.services.form_service import FormValidationError

        with pytest.raises(FormValidationError):
            await svc.create_report(
                ReportCreate(
                    name="Bad",
                    slug="bad",
                    entity_definition_id=deal.id,
                    # sum over a picklist is invalid — must be caught building the stmt
                    query=AggregateQuery(metrics=[Metric(op="sum", field="stage")]),
                    viz=Visualization(type="bar", series=[]),
                )
            )


class TestReportImportExport:
    async def test_report_roundtrips_with_entity_remap(self, admin_session: AsyncSession) -> None:
        source = await _make_org(admin_session, "rep-src")
        deal, _repo = await _seed_deals(admin_session, source)
        await ReportService(admin_session, source.id).create_report(
            ReportCreate(
                name="Won total",
                slug="won_total",
                entity_definition_id=deal.id,
                query=AggregateQuery(
                    group_by=[GroupBy(field="stage")],
                    metrics=[Metric(op="sum", field="amount", alias="total")],
                ),
                viz=Visualization(type="pie", x="stage", series=["total"]),
            )
        )
        await admin_session.commit()

        bundle = await MigrationExporter(admin_session, source.id).export()
        assert len(bundle["resources"]["reports"]) == 1

        target = await _make_org(admin_session, "rep-dst")
        summary = await MigrationImporter(admin_session, target.id, _settings()).import_bundle(
            bundle, CollisionStrategy.SKIP
        )
        await admin_session.commit()
        assert summary.resources["reports"].created == 1

        # The imported report binds to the target org's newly-created entity and runs.
        await set_tenant(admin_session, str(target.id))
        svc = ReportService(admin_session, target.id)
        reports = await svc.list_reports()
        assert len(reports) == 1
        result = await svc.run_report(reports[0].id)
        by_stage = {row["stage"]: Decimal(row["total"]) for row in result.rows}
        assert by_stage["won"] == Decimal(600)
