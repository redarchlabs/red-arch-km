"""Orchestration for views — composable screens rendered by the shared renderer.

A view reuses the form element tree. When it binds to an entity it validates and
renders exactly like a form (delegating to ``FormRenderService``); when it stands
alone (no entity) only presentational/action elements are allowed (labels,
buttons, ``form_ref`` embeds, and layout containers) and it renders with an empty
catalog. Reuses the form error hierarchy so the router maps errors uniformly.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.view import View
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
)
from api.repositories.view import ViewRepository
from api.schemas.form import FormConfig, FormRenderRead
from api.schemas.form_elements import iter_elements
from api.schemas.view import ViewCreate, ViewUpdate
from api.services.form_layout import LayoutError, validate
from api.services.form_service import (
    FormConflictError,
    FormNotFoundError,
    FormRenderService,
    FormValidationError,
    _load_layout_context,
)

MAX_VIEWS_PER_ORG = 200

# Elements that require an entity binding — illegal in a standalone (no-entity) view.
_ENTITY_BOUND_TYPES = {"field", "calculated", "section", "table", "block"}


class ViewService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._views = ViewRepository(session, org_id)
        self._defs = EntityDefinitionRepository(session, org_id)
        self._fields = EntityFieldRepository(session, org_id)

    async def list_views(self) -> list[View]:
        return await self._views.list_all()

    async def get_view(self, view_id: uuid.UUID) -> View:
        view = await self._views.get(view_id)
        if view is None:
            raise FormNotFoundError("view not found")
        return view

    async def _validate_config(
        self, entity_definition_id: uuid.UUID | None, config: FormConfig
    ) -> None:
        if entity_definition_id is None:
            # Standalone view: only presentational/action/layout elements allowed.
            for el, _depth in iter_elements(config.elements):
                if getattr(el, "type", None) in _ENTITY_BOUND_TYPES:
                    raise FormValidationError(
                        f"{el.type!r} elements require an entity-bound view"
                    )
            return
        root = await self._defs.get(entity_definition_id)
        if root is None:
            raise FormNotFoundError("entity not found")
        root_fields = await self._fields.list_for_definition(entity_definition_id)
        ctx = await _load_layout_context(self._session, self._org_id, root, root_fields, config)
        try:
            validate(config.elements, entity_definition_id, ctx.fields_by_entity(), ctx.rels)
        except LayoutError as exc:
            raise FormValidationError(str(exc)) from exc

    async def create_view(self, body: ViewCreate) -> View:
        if await self._views.count() >= MAX_VIEWS_PER_ORG:
            raise FormConflictError(f"max {MAX_VIEWS_PER_ORG} views per org")
        if body.entity_definition_id is not None and await self._defs.get(body.entity_definition_id) is None:
            raise FormNotFoundError("entity not found")
        if await self._views.get_by_slug(body.slug) is not None:
            raise FormConflictError(f"view slug already exists: {body.slug!r}")
        await self._validate_config(body.entity_definition_id, body.config)
        try:
            return await self._views.create(
                View(
                    id=uuid.uuid4(),
                    name=body.name,
                    slug=body.slug,
                    description=body.description,
                    entity_definition_id=body.entity_definition_id,
                    config=body.config.model_dump(mode="json"),
                )
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise FormConflictError(f"view slug already exists: {body.slug!r}") from exc

    async def update_view(self, view_id: uuid.UUID, body: ViewUpdate) -> View:
        view = await self.get_view(view_id)
        if body.config is not None:
            await self._validate_config(view.entity_definition_id, body.config)
            view.config = body.config.model_dump(mode="json")
        if body.name is not None:
            view.name = body.name
        if body.description is not None:
            view.description = body.description
        if body.is_active is not None:
            view.is_active = body.is_active
        await self._session.flush()
        return view

    async def delete_view(self, view_id: uuid.UUID) -> None:
        await self._views.delete(await self.get_view(view_id))

    async def render(self, view_id: uuid.UUID, record_id: uuid.UUID | None) -> FormRenderRead:
        view = await self.get_view(view_id)
        config = FormConfig.model_validate(view.config or {})
        if view.entity_definition_id is not None:
            # Entity-bound: render exactly like a form (reuse the shared core).
            renderer = FormRenderService(self._session, self._org_id)
            # Adapt: FormRenderService expects a Form-like object with name/description/
            # entity_definition_id/config — View has all of these attributes.
            return await renderer.build_render(view, record_id, "editable")  # type: ignore[arg-type]
        # Standalone: config only, empty catalog/values.
        return FormRenderRead(
            form_id=view.id,
            form_name=view.name,
            description=view.description,
            status="editable",
            root_entity_id=None,
            config=config,
            catalog=[],
            relationships=[],
            values={},
            related={},
        )
