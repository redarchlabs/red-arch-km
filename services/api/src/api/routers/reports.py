"""Report routes — the reporting engine's authoring + run surface.

Admin CRUD (org-admin) plus member-gated run endpoints: ``/{id}/run`` executes a
saved report (with optional filter/limit overrides for dashboard interactivity)
and ``/run`` executes an ad-hoc query for the report builder's live preview.
Reuses the form error hierarchy so failures map to the same HTTP codes as the
rest of the authoring surface.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.dependencies import get_tenant_db
from api.schemas.aggregate import AggregateResult
from api.schemas.report import (
    AdHocRunRequest,
    ReportCreate,
    ReportRead,
    ReportRunRequest,
    ReportUpdate,
)
from api.services.form_service import (
    FormConflictError,
    FormError,
    FormNotFoundError,
    FormValidationError,
)
from api.services.report_service import ReportService

router = APIRouter()

_ERROR_STATUS = {
    FormConflictError: status.HTTP_409_CONFLICT,
    FormNotFoundError: status.HTTP_404_NOT_FOUND,
    FormValidationError: status.HTTP_400_BAD_REQUEST,
}


def _raise_http(exc: FormError) -> None:
    code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/", response_model=list[ReportRead])
async def list_reports(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ReportRead]:
    reports = await ReportService(session, ctx.org_id).list_reports()
    return [ReportRead.model_validate(r) for r in reports]


@router.post("/", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
async def create_report(
    body: ReportCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ReportRead:
    try:
        report = await ReportService(session, ctx.org_id).create_report(body)
    except FormError as exc:
        _raise_http(exc)
    return ReportRead.model_validate(report)


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ReportRead:
    try:
        report = await ReportService(session, ctx.org_id).get_report(report_id)
    except FormError as exc:
        _raise_http(exc)
    return ReportRead.model_validate(report)


@router.patch("/{report_id}", response_model=ReportRead)
async def update_report(
    report_id: uuid.UUID,
    body: ReportUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ReportRead:
    try:
        report = await ReportService(session, ctx.org_id).update_report(report_id, body)
    except FormError as exc:
        _raise_http(exc)
    return ReportRead.model_validate(report)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    try:
        await ReportService(session, ctx.org_id).delete_report(report_id)
    except FormError as exc:
        _raise_http(exc)


@router.post("/run", response_model=AggregateResult)
async def run_adhoc(
    body: AdHocRunRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AggregateResult:
    """Run an unsaved aggregation — the report builder's live preview."""
    try:
        return await ReportService(session, ctx.org_id).run_adhoc(body.entity_definition_id, body.query)
    except FormError as exc:
        _raise_http(exc)


@router.post("/{report_id}/run", response_model=AggregateResult)
async def run_report(
    report_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    body: ReportRunRequest | None = None,
) -> AggregateResult:
    """Run a saved report, optionally with dashboard filter/limit overrides."""
    try:
        return await ReportService(session, ctx.org_id).run_report(report_id, body)
    except FormError as exc:
        _raise_http(exc)
