"""Orchestration for the reporting engine.

A :class:`ReportService` owns saved-report CRUD and running aggregations. Saved
reports and ad-hoc previews share one code path: build the tenant-scoped
:class:`DynamicEntityRepository` for the target entity and hand it an
:class:`AggregateQuery`. Query validity is checked at *save* time by building
(but not executing) the aggregate statement, so an invalid field/op/bucket is a
clean 400 rather than a broken saved report.

Reuses the form error hierarchy (``FormConflictError`` / ``FormNotFoundError`` /
``FormValidationError``) so the router maps errors uniformly with the rest of the
authoring surface.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.report import Report
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError
from api.repositories.report import ReportRepository
from api.schemas.aggregate import AggregateQuery, AggregateResult
from api.schemas.report import ReportCreate, ReportRunRequest, ReportUpdate
from api.services.form_service import (
    FormConflictError,
    FormNotFoundError,
    FormValidationError,
)

MAX_REPORTS_PER_ORG = 500


class ReportService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._reports = ReportRepository(session, org_id)
        self._defs = EntityDefinitionRepository(session, org_id)
        self._fields = EntityFieldRepository(session, org_id)
        self._rels = EntityRelationshipRepository(session, org_id)

    # -- CRUD ---------------------------------------------------------- #
    async def list_reports(self) -> list[Report]:
        return await self._reports.list_all()

    async def get_report(self, report_id: uuid.UUID) -> Report:
        report = await self._reports.get(report_id)
        if report is None:
            raise FormNotFoundError("report not found")
        return report

    async def create_report(self, body: ReportCreate) -> Report:
        if await self._reports.count() >= MAX_REPORTS_PER_ORG:
            raise FormConflictError(f"max {MAX_REPORTS_PER_ORG} reports per org")
        await self._validate_query(body.entity_definition_id, body.query)
        if await self._reports.get_by_slug(body.slug) is not None:
            raise FormConflictError(f"report slug already exists: {body.slug!r}")
        try:
            return await self._reports.create(
                Report(
                    id=uuid.uuid4(),
                    name=body.name,
                    slug=body.slug,
                    description=body.description,
                    entity_definition_id=body.entity_definition_id,
                    query=body.query.model_dump(mode="json"),
                    viz=body.viz.model_dump(mode="json"),
                )
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise FormConflictError(f"report slug already exists: {body.slug!r}") from exc

    async def update_report(self, report_id: uuid.UUID, body: ReportUpdate) -> Report:
        report = await self.get_report(report_id)
        if body.query is not None:
            await self._validate_query(report.entity_definition_id, body.query)
            report.query = body.query.model_dump(mode="json")
        if body.viz is not None:
            report.viz = body.viz.model_dump(mode="json")
        if body.name is not None:
            report.name = body.name
        if body.description is not None:
            report.description = body.description
        if body.is_active is not None:
            report.is_active = body.is_active
        await self._session.flush()
        return report

    async def delete_report(self, report_id: uuid.UUID) -> None:
        await self._reports.delete(await self.get_report(report_id))

    # -- Running ------------------------------------------------------- #
    async def run_report(self, report_id: uuid.UUID, overrides: ReportRunRequest | None = None) -> AggregateResult:
        report = await self.get_report(report_id)
        query = AggregateQuery.model_validate(report.query)
        if overrides is not None and (overrides.extra_filters or overrides.limit is not None):
            query = query.model_copy(
                update={
                    "filters": [*query.filters, *overrides.extra_filters],
                    "limit": overrides.limit if overrides.limit is not None else query.limit,
                }
            )
        return await self._run(report.entity_definition_id, query)

    async def run_adhoc(self, entity_definition_id: uuid.UUID, query: AggregateQuery) -> AggregateResult:
        return await self._run(entity_definition_id, query)

    # -- Internals ----------------------------------------------------- #
    async def _entity_repo(self, entity_definition_id: uuid.UUID) -> DynamicEntityRepository:
        definition = await self._defs.get(entity_definition_id)
        if definition is None or not definition.is_active:
            raise FormNotFoundError("entity not found")
        fields = await self._fields.list_for_definition(definition.id)
        rels = await self._rels.list_for_source(definition.id)
        return DynamicEntityRepository(self._session, self._org_id, definition, fields, rels)

    async def _validate_query(self, entity_definition_id: uuid.UUID, query: AggregateQuery) -> None:
        """Build (without executing) the aggregate statement so an invalid field
        slug / op / date-bucket is rejected at save time."""
        repo = await self._entity_repo(entity_definition_id)
        try:
            repo._build_aggregate(query)
        except EntityRecordError as exc:
            raise FormValidationError(str(exc)) from exc

    async def _run(self, entity_definition_id: uuid.UUID, query: AggregateQuery) -> AggregateResult:
        repo = await self._entity_repo(entity_definition_id)
        try:
            return await repo.aggregate(query)
        except EntityRecordError as exc:
            raise FormValidationError(str(exc)) from exc
