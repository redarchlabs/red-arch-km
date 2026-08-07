"""Organization CRUD routes."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import (
    CurrentUser,
    OrgContext,
    get_current_user,
    require_org_admin,
    require_site_admin,
)
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.repositories.org import OrgRepository
from api.repositories.view import ViewRepository
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.org import OrgCreate, OrgRead, OrgSettingsUpdate, OrgUpdate
from api.services.brain_client import BrainAPIClient
from api.services.crypto import encrypt_secret
from api.services.openai_client import model_routes

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=PaginatedResponse[OrgRead])
async def list_orgs(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[OrgRead]:
    repo = OrgRepository(session)
    if user.is_site_admin:
        orgs, total = await repo.list_all(offset=pagination.offset, limit=pagination.page_size)
    else:
        orgs, total = await repo.list_for_user(user.profile_id, offset=pagination.offset, limit=pagination.page_size)
    return make_page([OrgRead.model_validate(o) for o in orgs], total, pagination)


@router.get("/llm-models")
async def list_llm_models(
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """The model ids an org can be pinned to via ``default_llm_model``.

    Declared before ``GET /{org_id}`` so the literal path wins the route match.
    ``models`` lists every id with its own endpoint route (OPENAI_MODEL_ROUTES)
    plus the platform defaults; ``default`` is what an org with no override uses.
    """
    routed = sorted(model_routes(settings).keys())
    defaults = [settings.openai_model, settings.openai_summary_model]
    models = list(dict.fromkeys(routed + [m for m in defaults if m]))
    return {"default": settings.openai_model, "models": models}


@router.post("/", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OrgRead:
    repo = OrgRepository(session)
    org = await repo.create(
        name=body.name,
        description=body.description,
        use_knowledge_graph=body.use_knowledge_graph,
    )

    # Initialize tenant in vector/graph stores (best-effort, logged on failure).
    # Session commit happens in the get_db dependency on successful return.
    try:
        client = BrainAPIClient(settings)
        await client.init_tenant(str(org.id))
    except Exception as e:
        logger.error("Failed to initialize brain-api tenant for org %s: %s", org.id, e)

    return OrgRead.model_validate(org)


@router.get("/{org_id}", response_model=OrgRead)
async def get_org(
    org_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OrgRead:
    repo = OrgRepository(session)
    org = await repo.get(org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    # Visibility: site admin or member
    if not user.is_site_admin:
        user_orgs, _ = await repo.list_for_user(user.profile_id, limit=10_000)
        if org.id not in {o.id for o in user_orgs}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")

    return OrgRead.model_validate(org)


@router.patch("/{org_id}", response_model=OrgRead)
async def update_org(
    org_id: uuid.UUID,
    body: OrgUpdate,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OrgRead:
    repo = OrgRepository(session)
    org = await repo.get(org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    # Encrypt the per-org OpenAI key at rest before it touches the DB. An empty
    # string is passed through to clear the key; None means "no change".
    encrypted_key: str | None = None
    if body.openai_api_key is not None:
        encrypted_key = (
            encrypt_secret(body.openai_api_key, settings.org_encryption_key.get_secret_value())
            if body.openai_api_key
            else ""
        )

    org = await repo.update(
        org,
        name=body.name,
        description=body.description,
        use_knowledge_graph=body.use_knowledge_graph,
        openai_api_key=encrypted_key,
        # None = no change; empty string clears back to the platform default.
        default_llm_model=body.default_llm_model,
    )
    return OrgRead.model_validate(org)


@router.patch("/{org_id}/settings", response_model=OrgRead)
async def update_org_settings(
    org_id: uuid.UUID,
    body: OrgSettingsUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OrgRead:
    """Org settings an **org admin** owns — currently the home (landing) view.

    Split out from ``PATCH /orgs/{org_id}`` (site admin only) because the home
    view points at a view the org itself authored: choosing it is a tenant
    decision, not a platform one. Tenancy/cost fields stay on the site-admin
    endpoint.

    Like every org-admin route the caller's privileges come from the X-Org-ID
    header (``require_org_admin``); the path id must agree with it so a stale
    tab can't write settings to a different org than the one it is showing.
    """
    if org_id != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path organization does not match the active organization",
        )

    repo = OrgRepository(session)
    org = await repo.get(org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    # orgs.home_view_id has no FK (cross-schema, see docs/DATABASE.md), so the
    # ownership check is ours to make: without it an org admin could point their
    # landing screen at another tenant's view id.
    if body.home_view_id is not None and await ViewRepository(session, org_id).get(body.home_view_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="home_view_id does not reference a view in this organization",
        )

    # Replacement semantics: null/omitted clears the home view (OrgSettingsUpdate).
    org = await repo.set_home_view(org, body.home_view_id)
    return OrgRead.model_validate(org)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    org_id: uuid.UUID,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Delete an org. Cascades across PostgreSQL, Qdrant, and Neo4j.

    Site admin only. Destructive: wipes all data belonging to the org from:
      - PostgreSQL (via FK CASCADE on org_id)
      - Qdrant (both per-tenant collections)
      - Neo4j (all nodes with the tenant label)

    Cascade to brain-api is best-effort: if Qdrant/Neo4j cleanup fails,
    the PostgreSQL delete still completes and operators are expected to
    follow up via the logs. Blocking the DB delete on a transient infra
    outage would be worse than leaving orphan vectors behind.
    """
    repo = OrgRepository(session)
    deleted = await repo.delete(org_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    # Best-effort cascade to brain-api. Any failure here is logged but
    # does not abort the HTTP response — the session.commit() in get_db
    # will still persist the PostgreSQL delete.
    try:
        client = BrainAPIClient(settings)
        await client.remove_tenant(str(org_id))
    except Exception as e:
        logger.error(
            "brain-api tenant cleanup failed for deleted org %s: %s — manual cleanup may be required",
            org_id,
            e,
        )

    logger.warning("Site-admin deleted org %s", org_id)
