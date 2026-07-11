"""``/api/v1/reports`` — list, read, and run saved reports (+ ad-hoc aggregation).

Reuses :class:`ReportService`, so results are identical to the reporting engine in
the UI. Reads require ``reports:read``; executing a report or an ad-hoc query
requires ``reports:run``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_scope
from api.schemas.aggregate import AggregateResult
from api.schemas.report import AdHocRunRequest, ReportRead, ReportRunRequest
from api.services.form_service import FormError, FormNotFoundError, FormValidationError
from api.services.report_service import ReportService

router = APIRouter()

_ERROR_STATUS = {
    FormNotFoundError: status.HTTP_404_NOT_FOUND,
    FormValidationError: status.HTTP_400_BAD_REQUEST,
}


def _raise_http(exc: FormError) -> NoReturn:
    raise HTTPException(status_code=_ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST), detail=str(exc)) from exc


@router.get("", response_model=list[ReportRead])
async def list_reports(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("reports:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[ReportRead]:
    """List the org's saved reports."""
    reports = await ReportService(session, principal.org_id).list_reports()
    return [ReportRead.model_validate(r) for r in reports]


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("reports:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> ReportRead:
    """Fetch one saved report's definition."""
    try:
        report = await ReportService(session, principal.org_id).get_report(report_id)
    except FormError as exc:
        _raise_http(exc)
    return ReportRead.model_validate(report)


@router.post("/{report_id}/run", response_model=AggregateResult)
async def run_report(
    report_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("reports:run"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    overrides: ReportRunRequest | None = None,
) -> AggregateResult:
    """Execute a saved report, with optional filter/limit overrides."""
    try:
        return await ReportService(session, principal.org_id).run_report(report_id, overrides)
    except FormError as exc:
        _raise_http(exc)


@router.post("/run", response_model=AggregateResult)
async def run_adhoc(
    body: AdHocRunRequest,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("reports:run"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> AggregateResult:
    """Run an aggregation over an entity without saving a report."""
    try:
        return await ReportService(session, principal.org_id).run_adhoc(body.entity_definition_id, body.query)
    except FormError as exc:
        _raise_http(exc)
