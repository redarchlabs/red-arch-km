"""Orchestration for the flexible form designer.

Three collaborators share this module:

* ``FormService`` — authenticated, org-scoped admin operations (form CRUD +
  minting links) on the caller's tenant session.
* ``FormRenderService`` — the session-agnostic **render/submit core**. Given a
  session already scoped to an org, it resolves a form's element tree into a
  render payload and applies a submission (root + related writes, cross-entity
  editable columns, server-authoritative calculated values). Used by both the
  public token path and the authenticated internal fill surface.
* ``PublicFormService`` — the unauthenticated public path. It receives the
  *privileged* session, resolves the org from the token, drops to ``app_user``
  scoped to that org, then delegates to ``FormRenderService``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.models.custom_entity import EntityDefinition, EntityField
from api.models.form import Form, FormLink
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError
from api.repositories.form import (
    FormLinkRepository,
    FormRepository,
    resolve_link_by_token_hash,
    unusable_reason,
)
from api.repositories.workflow import OutboxWriter
from api.schemas.form import (
    EntityCatalogEntry,
    FieldMeta,
    FormConfig,
    FormCreate,
    FormRenderRead,
    FormSubmit,
    FormUpdate,
    GenerateLinkRequest,
    RelationshipMeta,
)
from api.services import form_expression, form_token
from api.services.email import EmailSender, render_intake_email
from api.services.form_layout import (
    Bindings,
    BlockBinding,
    RelInfo,
    SectionBinding,
    TableBinding,
    collect_relationship_ids,
    flatten,
    validate,
)

logger = logging.getLogger(__name__)

MAX_FORMS_PER_ORG = 200
MAX_SECTION_ROWS = 100


class FormError(Exception):
    """Base error for form orchestration."""


class FormNotFoundError(FormError):
    """Form / link / target record not found (HTTP 404)."""


class FormConflictError(FormError):
    """Slug already exists / per-org limit (HTTP 409)."""


class FormValidationError(FormError):
    """Invalid config or submission (HTTP 400)."""


class FormLinkError(FormError):
    """The link exists but can't be used (submitted/expired/revoked) (HTTP 410)."""


def _as_uuid(value: Any) -> uuid.UUID:
    """Coerce a client-supplied id to a UUID, raising a clean validation error
    (→ HTTP 400) instead of letting a bare ValueError become a 500."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise FormValidationError(f"invalid id: {value!r}") from exc


async def _scope_to_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Drop to app_user + set the tenant GUC (bypass off) so RLS scopes everything
    that follows to ``org_id``. The token→org lookup that precedes this runs on the
    get_db (bypass) session; here we downgrade before any scoped write. See db_scope."""
    await db_scope.enter_tenant(session, org_id)


# ------------------------------------------------------------------ #
# Shared layout context loading (fields + relationships for a tree)
# ------------------------------------------------------------------ #
class _LayoutContext:
    """Entity fields, definitions, and relationship info for every entity the
    tree touches — loaded once and reused for validation, render, and submit."""

    def __init__(
        self,
        defs: dict[uuid.UUID, EntityDefinition],
        fields: dict[uuid.UUID, list[EntityField]],
        rels: dict[uuid.UUID, RelInfo],
        rel_names: dict[uuid.UUID, str],
        rel_slugs: dict[uuid.UUID, str],
    ) -> None:
        self.defs = defs
        self.fields = fields
        self.rels = rels
        self.rel_names = rel_names
        self.rel_slugs = rel_slugs  # relationship id -> FK column slug (on the owning entity)

    def fields_by_entity(self) -> dict[uuid.UUID, set[str]]:
        return {eid: {f.slug for f in flds} for eid, flds in self.fields.items()}


async def _load_layout_context(
    session: AsyncSession,
    org_id: uuid.UUID,
    root: EntityDefinition,
    root_fields: list[EntityField],
    config: FormConfig,
) -> _LayoutContext:
    defs_repo = EntityDefinitionRepository(session, org_id)
    fields_repo = EntityFieldRepository(session, org_id)
    rels_repo = EntityRelationshipRepository(session, org_id)

    defs: dict[uuid.UUID, EntityDefinition] = {root.id: root}
    fields: dict[uuid.UUID, list[EntityField]] = {root.id: root_fields}
    rels: dict[uuid.UUID, RelInfo] = {}
    rel_names: dict[uuid.UUID, str] = {}
    rel_slugs: dict[uuid.UUID, str] = {}

    async def ensure_entity(entity_id: uuid.UUID) -> None:
        if entity_id in defs:
            return
        definition = await defs_repo.get(entity_id)
        if definition is None:
            return
        defs[entity_id] = definition
        fields[entity_id] = await fields_repo.list_for_definition(entity_id)

    for rel_id in collect_relationship_ids(config.elements):
        rel = await rels_repo.get(rel_id)
        if rel is None:
            continue
        rels[rel_id] = RelInfo(source_id=rel.source_definition_id, target_id=rel.target_definition_id)
        rel_names[rel_id] = rel.name
        rel_slugs[rel_id] = rel.slug
        await ensure_entity(rel.source_definition_id)
        await ensure_entity(rel.target_definition_id)

    return _LayoutContext(defs, fields, rels, rel_names, rel_slugs)


class FormService:
    """Authenticated, org-scoped admin operations on the caller's tenant session."""

    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        *,
        public_base_url: str = "",
        email_sender: EmailSender | None = None,
    ) -> None:
        self._session = session
        self._org_id = org_id
        self._public_base_url = public_base_url.rstrip("/")
        self._email_sender = email_sender
        self._forms = FormRepository(session, org_id)
        self._links = FormLinkRepository(session, org_id)
        self._defs = EntityDefinitionRepository(session, org_id)
        self._fields = EntityFieldRepository(session, org_id)
        self._rels = EntityRelationshipRepository(session, org_id)

    async def list_forms(self) -> list[Form]:
        return await self._forms.list_all()

    async def get_form(self, form_id: uuid.UUID) -> Form:
        form = await self._forms.get(form_id)
        if form is None:
            raise FormNotFoundError("form not found")
        return form

    async def _validate_config(self, entity_definition_id: uuid.UUID, config: FormConfig) -> None:
        root = await self._defs.get(entity_definition_id)
        if root is None:
            raise FormNotFoundError("entity not found")
        root_fields = await self._fields.list_for_definition(entity_definition_id)
        ctx = await _load_layout_context(self._session, self._org_id, root, root_fields, config)
        from api.services.form_layout import LayoutError

        try:
            validate(config.elements, entity_definition_id, ctx.fields_by_entity(), ctx.rels)
        except LayoutError as exc:
            raise FormValidationError(str(exc)) from exc

    async def validate_layout(self, entity_definition_id: uuid.UUID, config: FormConfig) -> None:
        """Validate a layout tree against an entity WITHOUT persisting.

        Public entry point for dry-run validation (agent ``validate_form_layout``
        tool). Raises ``FormError`` (FormNotFoundError / FormValidationError) with
        an actionable message; returns None when the tree is valid.
        """
        await self._validate_config(entity_definition_id, config)

    async def create_form(self, body: FormCreate) -> Form:
        if await self._forms.count() >= MAX_FORMS_PER_ORG:
            raise FormConflictError(f"max {MAX_FORMS_PER_ORG} forms per org")
        if await self._defs.get(body.entity_definition_id) is None:
            raise FormNotFoundError("entity not found")
        if await self._forms.get_by_slug(body.slug) is not None:
            raise FormConflictError(f"form slug already exists: {body.slug!r}")
        await self._validate_config(body.entity_definition_id, body.config)
        try:
            return await self._forms.create(
                Form(
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
            raise FormConflictError(f"form slug already exists: {body.slug!r}") from exc

    async def update_form(self, form_id: uuid.UUID, body: FormUpdate) -> Form:
        form = await self.get_form(form_id)
        if body.config is not None:
            await self._validate_config(form.entity_definition_id, body.config)
            form.config = body.config.model_dump(mode="json")
        if body.name is not None:
            form.name = body.name
        if body.description is not None:
            form.description = body.description
        if body.is_active is not None:
            form.is_active = body.is_active
        await self._session.flush()
        return form

    async def delete_form(self, form_id: uuid.UUID) -> None:
        await self._forms.delete(await self.get_form(form_id))

    async def generate_link(
        self, form_id: uuid.UUID, body: GenerateLinkRequest
    ) -> tuple[FormLink, str, str, bool]:
        """Mint a single-use link for ``target_record_id``. Returns
        ``(link, raw_token, url, email_sent)`` — the raw token is only available here."""
        form = await self.get_form(form_id)
        definition = await self._defs.get(form.entity_definition_id)
        if definition is None:
            raise FormNotFoundError("entity not found")
        repo = await self._build_repo(definition)
        if await repo.get(body.target_record_id) is None:
            raise FormNotFoundError("target record not found")

        raw_token, token_hash = form_token.generate_token()
        expires_at = (
            datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
        )
        link = await self._links.create(
            FormLink(
                id=uuid.uuid4(),
                form_id=form.id,
                target_record_id=body.target_record_id,
                token_hash=token_hash,
                recipient_email=body.recipient_email,
                expires_at=expires_at,
            )
        )
        url = self.link_url(raw_token)
        email_sent = await self._maybe_email(body.recipient_email, form.name, url)
        return link, raw_token, url, email_sent

    async def _maybe_email(self, recipient: str | None, form_name: str, url: str) -> bool:
        if not recipient or self._email_sender is None or not self._email_sender.is_configured():
            return False
        subject, body_text, html = render_intake_email(form_name=form_name, url=url)
        try:
            await self._email_sender.send(to=recipient, subject=subject, text=body_text, html=html)
        except Exception:  # noqa: BLE001 - a send failure must not void a usable link
            logger.warning("intake email to %s failed; link still usable", recipient, exc_info=True)
            return False
        return True

    async def list_links(self, form_id: uuid.UUID) -> list[FormLink]:
        await self.get_form(form_id)  # 404s if not in this org
        return await self._links.list_for_form(form_id)

    async def revoke_link(self, form_id: uuid.UUID, link_id: uuid.UUID) -> FormLink:
        """Invalidate a pending link before its natural expiry (e.g. a leaked
        token). A submitted link can't be revoked; an already-revoked/expired one
        is returned unchanged (idempotent)."""
        await self.get_form(form_id)  # 404s if the form isn't in this org
        link = await self._links.get(link_id)
        if link is None or link.form_id != form_id:
            raise FormNotFoundError("link not found")
        if link.status == "submitted":
            raise FormValidationError("a submitted link cannot be revoked")
        if link.status == "pending":
            link.status = "revoked"
            await self._session.flush()
        return link

    def link_url(self, raw_token: str) -> str:
        return f"{self._public_base_url}/intake/{raw_token}"

    async def _build_repo(self, definition: EntityDefinition) -> DynamicEntityRepository:
        fields = await self._fields.list_for_definition(definition.id)
        rels = await self._rels.list_for_source(definition.id)
        return DynamicEntityRepository(self._session, self._org_id, definition, fields, rels)


class FormRenderService:
    """Session-agnostic render/submit core. The session must already be scoped to
    ``org_id`` (the public path scopes via the token; the authenticated path uses
    the caller's tenant session)."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._defs = EntityDefinitionRepository(session, org_id)
        self._fields = EntityFieldRepository(session, org_id)
        self._rels = EntityRelationshipRepository(session, org_id)
        self._repo_cache: dict[uuid.UUID, DynamicEntityRepository] = {}

    async def _repo(self, entity_id: uuid.UUID) -> DynamicEntityRepository:
        cached = self._repo_cache.get(entity_id)
        if cached is not None:
            return cached
        definition = await self._defs.get(entity_id)
        if definition is None:
            raise FormNotFoundError("entity not found")
        fields = await self._fields.list_for_definition(entity_id)
        rels = await self._rels.list_for_source(entity_id)
        repo = DynamicEntityRepository(
            self._session, self._org_id, definition, fields, rels,
            outbox=OutboxWriter(self._session), outbox_source="form",
        )
        self._repo_cache[entity_id] = repo
        return repo

    async def _context(self, form: Form, config: FormConfig) -> tuple[_LayoutContext, Bindings]:
        root = await self._defs.get(form.entity_definition_id)
        if root is None:
            raise FormNotFoundError("entity not found")
        root_fields = await self._fields.list_for_definition(root.id)
        ctx = await _load_layout_context(self._session, self._org_id, root, root_fields, config)
        return ctx, flatten(config.elements, ctx.rels)

    # ---- render -------------------------------------------------------- #
    async def build_render(
        self, form: Form, target_record_id: uuid.UUID | None, status: str
    ) -> FormRenderRead:
        config = FormConfig.model_validate(form.config or {})
        ctx, bindings = await self._context(form, config)

        catalog = self._build_catalog(form.entity_definition_id, ctx, bindings)
        relationships = self._build_relationship_meta(ctx, bindings)

        values: dict[str, Any] = {}
        related: dict[str, Any] = {}
        root_record: dict[str, Any] | None = None
        if target_record_id is not None:
            root_record = await (await self._repo(form.entity_definition_id)).get(target_record_id)
            if root_record is not None:
                values = {s: root_record.get(s) for s in bindings.root.display_slugs}
                related = await self._prefill_related(bindings, ctx, root_record, target_record_id)

        return FormRenderRead(
            form_id=form.id,
            form_name=form.name,
            description=form.description,
            status=status,
            root_entity_id=form.entity_definition_id,
            config=config,
            catalog=catalog,
            relationships=relationships,
            values=values,
            related=related,
        )

    def _build_catalog(
        self, root_id: uuid.UUID, ctx: _LayoutContext, bindings: Bindings
    ) -> list[EntityCatalogEntry]:
        needed: set[uuid.UUID] = {root_id}
        for c in bindings.containers:
            needed.add(c.entity_id)
            if isinstance(c, TableBinding):
                needed.update(rc.entity_id for rc in c.related_cols)
        out: list[EntityCatalogEntry] = []
        for eid in needed:
            definition = ctx.defs.get(eid)
            if definition is None:
                continue
            out.append(
                EntityCatalogEntry(
                    entity_id=eid,
                    name=definition.name,
                    fields=[
                        FieldMeta(
                            slug=f.slug,
                            label=f.name,
                            field_type=f.field_type,
                            required=f.is_required,
                            options=list(f.picklist_options or []),
                        )
                        for f in ctx.fields.get(eid, [])
                    ],
                )
            )
        return out

    def _build_relationship_meta(
        self, ctx: _LayoutContext, bindings: Bindings
    ) -> list[RelationshipMeta]:
        out: list[RelationshipMeta] = []
        seen: set[uuid.UUID] = set()

        def add(rel_id: uuid.UUID, entity_id: uuid.UUID, kind: str) -> None:
            if rel_id in seen or rel_id not in ctx.rels:
                return
            seen.add(rel_id)
            out.append(
                RelationshipMeta(
                    relationship_id=rel_id,
                    related_entity_id=entity_id,
                    kind=kind,
                    name=ctx.rel_names.get(rel_id, ""),
                )
            )

        for c in bindings.containers:
            if isinstance(c, SectionBinding):
                add(c.rel_id, c.entity_id, "to_one")
            elif isinstance(c, (TableBinding, BlockBinding)):
                add(c.rel_id, c.entity_id, "to_many")
                if isinstance(c, TableBinding):
                    for rc in c.related_cols:
                        add(rc.rel_id, rc.entity_id, "to_one")
        return out

    async def _prefill_related(
        self,
        bindings: Bindings,
        ctx: _LayoutContext,
        root_record: dict[str, Any],
        root_id: uuid.UUID,
    ) -> dict[str, Any]:
        related: dict[str, Any] = {}
        for c in bindings.containers:
            rel_slug = self._rel_slug(ctx, c.rel_id)
            if rel_slug is None:
                continue
            if isinstance(c, SectionBinding):
                linked_id = root_record.get(rel_slug)
                if linked_id is None:
                    continue
                repo = await self._repo(c.entity_id)
                linked = await repo.get(uuid.UUID(str(linked_id)))
                if linked is not None:
                    related[str(c.rel_id)] = {
                        "id": str(linked.get("id")),
                        "values": {s: linked.get(s) for s in c.display_slugs},
                    }
            else:  # table or block — 1:M rows keyed by child FK = root_id
                repo = await self._repo(c.entity_id)
                items, _ = await repo.list(filters={rel_slug: root_id}, limit=MAX_SECTION_ROWS)
                rows = []
                for item in items:
                    row: dict[str, Any] = {
                        "id": str(item.get("id")),
                        "values": {s: item.get(s) for s in c.display_slugs},
                    }
                    if isinstance(c, TableBinding) and c.related_cols:
                        row["related"] = await self._prefill_row_related(c, ctx, item)
                    rows.append(row)
                related[str(c.rel_id)] = {"rows": rows}
        return related

    async def _prefill_row_related(
        self, c: TableBinding, ctx: _LayoutContext, child_row: dict[str, Any]
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for rc in c.related_cols:
            rc_slug = self._rel_slug(ctx, rc.rel_id)
            if rc_slug is None:
                continue
            linked_id = child_row.get(rc_slug)
            if linked_id is None:
                continue
            repo = await self._repo(rc.entity_id)
            linked = await repo.get(uuid.UUID(str(linked_id)))
            if linked is not None:
                out[str(rc.rel_id)] = {"id": str(linked.get("id")), "values": {rc.slug: linked.get(rc.slug)}}
        return out

    def _rel_slug(self, ctx: _LayoutContext, rel_id: uuid.UUID) -> str | None:
        """The relationship's FK column slug — how ``DynamicEntityRepository`` keys
        the FK on the owning entity (root for a section, child for a table/block)."""
        return ctx.rel_slugs.get(rel_id)

    # ---- submit -------------------------------------------------------- #
    async def apply_submit(
        self, form: Form, target_record_id: uuid.UUID, payload: FormSubmit
    ) -> None:
        config = FormConfig.model_validate(form.config or {})
        ctx, bindings = await self._context(form, config)
        try:
            root_repo = await self._repo(form.entity_definition_id)
            current = await root_repo.get(target_record_id)
            if current is None:
                raise FormNotFoundError("target record not found")
            clean = {k: v for k, v in payload.values.items() if k in bindings.root.write_slugs}
            # SECURITY: the calc context is the persisted record (truth for read-only
            # and non-form fields) overlaid with the caller's *write-filtered* values —
            # NEVER the raw payload, or a caller could spoof read-only inputs to forge
            # a "server-authoritative" calculated result.
            self._apply_calc(clean, bindings.root.calc, {**current, **clean})
            updated = await root_repo.update(target_record_id, clean)
            if updated is None:
                raise FormNotFoundError("target record not found")
            await self._submit_related(
                bindings, ctx, form.entity_definition_id, target_record_id, updated, payload.related
            )
        except EntityRecordError as exc:
            raise FormValidationError(str(exc)) from exc

    def _apply_calc(self, clean: dict[str, Any], calc: list[Any], context: dict[str, Any]) -> None:
        """Recompute persisted calculated values server-side. ``context`` MUST be a
        trusted base (persisted record + write-filtered values), never the raw
        client payload — see the callers."""
        for cb in calc:
            clean[cb.target_slug] = form_expression.evaluate(cb.expression, context)

    async def _submit_related(
        self,
        bindings: Bindings,
        ctx: _LayoutContext,
        root_entity_id: uuid.UUID,
        root_id: uuid.UUID,
        root_record: dict[str, Any],
        submitted: dict[str, Any],
    ) -> None:
        for c in bindings.containers:
            rel_slug = self._rel_slug(ctx, c.rel_id)
            if rel_slug is None:
                continue
            data = submitted.get(str(c.rel_id)) or {}
            if isinstance(c, SectionBinding):
                await self._write_section(c, rel_slug, root_entity_id, root_id, root_record, data)
            else:
                rows = data.get("rows") or []
                await self._write_rows(c, ctx, rel_slug, root_id, rows)

    async def _write_section(
        self,
        c: SectionBinding,
        rel_slug: str,
        root_entity_id: uuid.UUID,
        root_id: uuid.UUID,
        root_record: dict[str, Any],
        data: dict[str, Any],
    ) -> None:
        raw = data.get("values") or {}
        clean = {k: v for k, v in raw.items() if k in c.write_slugs}
        repo = await self._repo(c.entity_id)
        linked_id = root_record.get(rel_slug)  # server-side FK, trusted
        if linked_id is not None:
            current = await repo.get(_as_uuid(linked_id)) or {}
            self._apply_calc(clean, c.calc, {**current, **clean})
            if not clean:
                return
            await repo.update(_as_uuid(linked_id), clean)
            return
        # No linked record yet: create one and point the root's FK at it.
        self._apply_calc(clean, c.calc, clean)
        if not clean:
            return
        created = await repo.create(clean)
        root_repo = await self._repo(root_entity_id)
        await root_repo.update(root_id, {rel_slug: created["id"]})

    async def _write_rows(
        self, c: Any, ctx: _LayoutContext, rel_slug: str, root_id: uuid.UUID, rows: list[Any]
    ) -> None:
        if len(rows) > MAX_SECTION_ROWS:
            raise FormValidationError(f"too many rows (max {MAX_SECTION_ROWS})")
        repo = await self._repo(c.entity_id)
        related_cols = getattr(c, "related_cols", [])
        calc = getattr(c, "calc", [])
        for row in rows:
            raw = row.get("values") or {}
            clean = {k: v for k, v in raw.items() if k in c.write_slugs}
            row_id = row.get("id")
            if row_id:
                child_id = _as_uuid(row_id)
                existing = await repo.get(child_id)
                if existing is None or str(existing.get(rel_slug)) != str(root_id):
                    continue  # ownership check: only touch rows that belong to root
                self._apply_calc(clean, calc, {**existing, **clean})
                await repo.update(child_id, clean)
                child_current: dict[str, Any] | None = existing
            else:
                self._apply_calc(clean, calc, clean)
                created = await repo.create({rel_slug: root_id, **clean})
                child_id = _as_uuid(created["id"])
                child_current = None
            await self._write_row_related(
                repo, ctx, related_cols, child_id, child_current, row.get("related") or {}
            )

    async def _write_row_related(
        self,
        child_repo: DynamicEntityRepository,
        ctx: _LayoutContext,
        related_cols: list[Any],
        child_id: uuid.UUID,
        child_current: dict[str, Any] | None,
        row_related: dict[str, Any],
    ) -> None:
        for rc in related_cols:
            if not rc.editable:
                continue
            rc_slug = self._rel_slug(ctx, rc.rel_id)
            if rc_slug is None:
                continue
            rdata = row_related.get(str(rc.rel_id)) or {}
            raw = rdata.get("values") or {}
            clean = {k: v for k, v in raw.items() if k == rc.slug}
            if not clean:
                continue
            repo = await self._repo(rc.entity_id)
            linked_id = rdata.get("id")
            # SECURITY (IDOR): only update the related record CURRENTLY linked from
            # this child row via rc_slug. A missing/forged/mismatched id can never
            # hijack an arbitrary record — it creates a fresh one and re-points the FK.
            current_link = child_current.get(rc_slug) if child_current else None
            if linked_id and current_link is not None and str(linked_id) == str(current_link):
                await repo.update(_as_uuid(linked_id), clean)
            else:
                created = await repo.create(clean)
                await child_repo.update(child_id, {rc_slug: created["id"]})


class PublicFormService:
    """Unauthenticated public path. Receives the PRIVILEGED session; scopes to
    the token's org before any tenant data is read or written."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _resolve(self, raw_token: str) -> tuple[FormLink, Form]:
        link = await resolve_link_by_token_hash(self._session, form_token.hash_token(raw_token))
        if link is None:
            raise FormNotFoundError("form link not found")
        await _scope_to_org(self._session, link.org_id)
        form = await FormRepository(self._session, link.org_id).get(link.form_id)
        if form is None or not form.is_active:
            raise FormNotFoundError("form not found")
        return link, form

    async def load(self, raw_token: str) -> FormRenderRead:
        link, form = await self._resolve(raw_token)
        status = link.status
        if unusable_reason(link, datetime.now(UTC)) is not None and status == "pending":
            status = "expired"
        render = FormRenderService(self._session, link.org_id)
        return await render.build_render(form, link.target_record_id, status)

    async def submit(self, raw_token: str, payload: FormSubmit) -> None:
        link, form = await self._resolve(raw_token)
        reason = unusable_reason(link, datetime.now(UTC))
        if reason is not None:
            raise FormLinkError(reason)
        if link.target_record_id is None:
            raise FormValidationError("link has no target record")

        # Single-use, race-safe: atomically flip pending -> submitted. The loser of
        # two concurrent submissions sees 0 rows and is rejected.
        claimed = (
            await self._session.execute(
                text(
                    "UPDATE form_links SET status='submitted', submitted_at=now() "
                    "WHERE id=:id AND status='pending' RETURNING id"
                ),
                {"id": link.id},
            )
        ).first()
        if claimed is None:
            raise FormLinkError("This form has already been submitted.")

        render = FormRenderService(self._session, link.org_id)
        await render.apply_submit(form, link.target_record_id, payload)
        link.status = "submitted"
        link.submitted_at = datetime.now(UTC)
        await self._session.flush()
