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
from api.schemas.aggregate import AggregateQuery, FilterSpec, GroupBy, HavingSpec, Metric, OrderSpec
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.schemas.report import ReportCreate, ReportRunRequest, Visualization
from api.services.entity_service import EntityService
from api.services.form_service import FormNotFoundError
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


async def _seed_scored(admin_session: AsyncSession, org: Org):  # type: ignore[no-untyped-def]
    """A Lead entity with a nullable integer 'score' — ties (10,10) and NULLs, to
    exercise keyset pagination under a custom sort."""
    await set_tenant(admin_session, str(org.id))
    svc = EntityService(admin_session, org.id)
    lead = await svc.create_definition(
        EntityDefinitionCreate(
            name="Lead",
            slug="lead",
            fields=[EntityFieldCreate(name="Score", slug="score", field_type="integer")],
        )
    )
    await admin_session.commit()
    repo = await _load_repo(admin_session, org.id, lead.id)
    for score in (10, 10, 20, None, None, 30):
        await repo.create({"score": score} if score is not None else {})
    await admin_session.commit()
    return lead, repo


class TestKeysetCustomSort:
    async def _walk_all(self, repo, **kw):  # type: ignore[no-untyped-def]
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            items, cursor = await repo.list(cursor=cursor, limit=2, **kw)
            seen.extend(str(r["id"]) for r in items)
            pages += 1
            if cursor is None:
                break
            assert pages < 20
        return seen

    async def test_descending_custom_sort_with_ties_and_nulls(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "keyset-desc")
        _lead, repo = await _seed_scored(admin_session, org)
        seen = await self._walk_all(repo, order_by="score", order_dir="desc")
        assert len(seen) == 6  # every row once
        assert len(set(seen)) == 6  # no duplicate, no gap across the tie/NULL boundaries

    async def test_ascending_custom_sort_with_ties_and_nulls(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "keyset-asc")
        _lead, repo = await _seed_scored(admin_session, org)
        seen = await self._walk_all(repo, order_by="score", order_dir="asc")
        assert len(set(seen)) == 6

    async def test_picklist_tie_sort(self, admin_session: AsyncSession) -> None:
        # stage has heavy ties (won×3, open×2, lost×1) — pagination must not drop rows.
        org = await _make_org(admin_session, "keyset-ties")
        _deal, repo = await _seed_deals(admin_session, org)
        seen = await self._walk_all(repo, order_by="stage", order_dir="asc")
        assert len(set(seen)) == 6


class TestCrossOrgIsolation:
    async def test_aggregate_scoped_to_org(self, admin_session: AsyncSession) -> None:
        org_a = await _make_org(admin_session, "iso-agg-a")
        _deal_a, repo_a = await _seed_deals(admin_session, org_a)
        org_b = await _make_org(admin_session, "iso-agg-b")
        await _seed_deals(admin_session, org_b)
        # repo_a is bound to org A; its explicit org_id filter must exclude org B's rows.
        res = await repo_a.aggregate(AggregateQuery(metrics=[Metric(op="count", alias="c")]))
        assert res.rows[0]["c"] == 6  # A's 6, not 12

    async def test_report_of_other_org_not_found(self, admin_session: AsyncSession) -> None:
        org_a = await _make_org(admin_session, "iso-rep-a")
        deal_a, _repo = await _seed_deals(admin_session, org_a)
        report = await ReportService(admin_session, org_a.id).create_report(
            ReportCreate(
                name="A", slug="a", entity_definition_id=deal_a.id,
                query=AggregateQuery(metrics=[Metric(op="count", alias="c")]),
                viz=Visualization(type="metric", series=["c"]),
            )
        )
        await admin_session.commit()
        org_b = await _make_org(admin_session, "iso-rep-b")
        with pytest.raises(FormNotFoundError):
            await ReportService(admin_session, org_b.id).get_report(report.id)


class TestRunOverrides:
    async def test_extra_filter_narrows_result(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "run-ov")
        deal, _repo = await _seed_deals(admin_session, org)
        svc = ReportService(admin_session, org.id)
        report = await svc.create_report(
            ReportCreate(
                name="Pipeline", slug="pipeline", entity_definition_id=deal.id,
                query=AggregateQuery(group_by=[GroupBy(field="stage")], metrics=[Metric(op="count", alias="c")]),
                viz=Visualization(type="bar", x="stage", series=["c"]),
            )
        )
        await admin_session.commit()
        res = await svc.run_report(
            report.id, ReportRunRequest(extra_filters=[FilterSpec(field="stage", op="eq", value="won")])
        )
        assert {row["stage"] for row in res.rows} == {"won"}


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

    async def test_null_group_key_surfaces(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "agg-null")
        _lead, repo = await _seed_scored(admin_session, org)
        res = await repo.aggregate(
            AggregateQuery(group_by=[GroupBy(field="score")], metrics=[Metric(op="count", alias="c")])
        )
        by_score = {row["score"]: row["c"] for row in res.rows}
        # scores 10,10,20,None,None,30 → the two NULLs form their own group keyed None.
        assert by_score.get(None) == 2
        assert by_score.get(10) == 2

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
