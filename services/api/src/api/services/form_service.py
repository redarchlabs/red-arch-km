"""Orchestration for intake forms.

Two services share this module:

* ``FormService`` — authenticated, org-scoped admin operations (form CRUD +
  minting links). Runs on the caller's tenant session.
* ``PublicFormService`` — the unauthenticated public path. It receives the
  *privileged* session (no tenant context yet), resolves the org from the
  token, then drops to ``app_user`` with that org as the tenant GUC before
  touching any entity data. Everything after the token resolve is RLS-scoped.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.custom_entity import EntityDefinition, EntityField, EntityRelationship
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
    FormConfig,
    FormCreate,
    FormFieldConfig,
    FormSectionConfig,
    FormUpdate,
    GenerateLinkRequest,
    PublicFormField,
    PublicFormRead,
    PublicFormSection,
    PublicFormSubmit,
)
from api.services import form_token
from api.services.email import EmailSender, render_intake_email

logger = logging.getLogger(__name__)

MAX_FORMS_PER_ORG = 200


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


async def _scope_to_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Drop to app_user + set the tenant GUC so RLS scopes everything that
    follows to ``org_id`` (mirrors get_tenant_db, but org comes from the token)."""
    await session.execute(text("SET LOCAL ROLE app_user"))
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(org_id)}
    )


def _public_fields(
    field_configs: list[FormFieldConfig], fields: list[EntityField]
) -> list[PublicFormField]:
    """Resolve chosen field slugs against an entity's catalog, in declared order.
    Unknown slugs are skipped defensively (a deleted field can't crash a form)."""
    by_slug = {f.slug: f for f in fields}
    out: list[PublicFormField] = []
    for fc in field_configs:
        field = by_slug.get(fc.slug)
        if field is None:
            continue
        out.append(
            PublicFormField(
                slug=field.slug,
                label=fc.label or field.name,
                field_type=field.field_type,
                required=fc.required if fc.required is not None else field.is_required,
                help_text=fc.help_text,
                options=list(field.picklist_options or []),
                placeholder=fc.placeholder,
                width=fc.width,
                heading=fc.heading,
            )
        )
    return out


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
        fields = await self._fields.list_for_definition(entity_definition_id)
        valid_slugs = {f.slug for f in fields}
        for fc in config.fields:
            if fc.slug not in valid_slugs:
                raise FormValidationError(f"unknown field: {fc.slug!r}")
        # Valid section relationships are either the root's own to-one FKs (1:1)
        # or relationships from other entities that TARGET the root (1:M children).
        outgoing = {r.id for r in await self._rels.list_for_source(entity_definition_id)}
        incoming = {r.id for r in await self._rels.list_targeting(entity_definition_id)}
        valid_rels = outgoing | incoming
        for section in config.sections:
            if section.relationship_id not in valid_rels:
                raise FormValidationError(f"unknown relationship: {section.relationship_id}")

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
            # TOCTOU: the get_by_slug check above races a concurrent create with
            # the same (org_id, slug). The unique constraint is the source of
            # truth — turn its violation into a clean 409 rather than a 500.
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
        ``(link, raw_token, url, email_sent)`` — the raw token is only available here.

        If a recipient email is given and SMTP is configured, the invitation is
        emailed. A send failure never fails link creation (the link is still
        usable via its URL) — ``email_sent`` reports whether delivery happened."""
        form = await self.get_form(form_id)
        definition = await self._defs.get(form.entity_definition_id)
        if definition is None:
            raise FormNotFoundError("entity not found")
        repo = await self._build_repo(definition)
        if await repo.get(body.target_record_id) is None:
            raise FormNotFoundError("target record not found")

        raw_token, token_hash = form_token.generate_token()
        expires_at = (
            datetime.now(UTC) + timedelta(days=body.expires_in_days)
            if body.expires_in_days
            else None
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
        subject, text, html = render_intake_email(form_name=form_name, url=url)
        try:
            await self._email_sender.send(to=recipient, subject=subject, text=text, html=html)
        except Exception:  # noqa: BLE001 - a send failure must not void a usable link
            logger.warning("intake email to %s failed; link still usable", recipient, exc_info=True)
            return False
        return True

    async def list_links(self, form_id: uuid.UUID) -> list[FormLink]:
        await self.get_form(form_id)  # 404s if not in this org
        return await self._links.list_for_form(form_id)

    def link_url(self, raw_token: str) -> str:
        # Public intake page (unauthenticated); distinct from the admin /forms UI.
        return f"{self._public_base_url}/intake/{raw_token}"

    async def _build_repo(self, definition: EntityDefinition) -> DynamicEntityRepository:
        fields = await self._fields.list_for_definition(definition.id)
        rels = await self._rels.list_for_source(definition.id)
        return DynamicEntityRepository(self._session, self._org_id, definition, fields, rels)


@dataclass
class _ResolvedLink:
    link: FormLink
    form: Form
    definition: EntityDefinition
    fields: list[EntityField]
    root_rels: list[EntityRelationship]


@dataclass
class _SectionMeta:
    """A form section resolved against the catalog. For a 1:1 (inline/modal)
    section the relationship's FK lives on the ROOT record; for a 1:M (table)
    section the FK (``rel.slug``) lives on each CHILD record and points at root."""

    rel: EntityRelationship
    related_def: EntityDefinition
    related_fields: list[EntityField]
    public_fields: list[PublicFormField]
    is_table: bool
    mode: str

    @property
    def exposed_slugs(self) -> set[str]:
        return {f.slug for f in self.public_fields}


# Bound the number of child rows a single submission may create/update.
MAX_SECTION_ROWS = 100


class PublicFormService:
    """Unauthenticated public path. Receives the PRIVILEGED session; scopes to
    the token's org before any tenant data is read or written."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _resolve(self, raw_token: str) -> _ResolvedLink:
        link = await resolve_link_by_token_hash(self._session, form_token.hash_token(raw_token))
        if link is None:
            raise FormNotFoundError("form link not found")
        # Everything past the token resolve is RLS-scoped to the link's org.
        await _scope_to_org(self._session, link.org_id)
        org_id = link.org_id
        form = await FormRepository(self._session, org_id).get(link.form_id)
        if form is None or not form.is_active:
            raise FormNotFoundError("form not found")
        definition = await EntityDefinitionRepository(self._session, org_id).get(form.entity_definition_id)
        if definition is None:
            raise FormNotFoundError("entity not found")
        fields = await EntityFieldRepository(self._session, org_id).list_for_definition(definition.id)
        root_rels = await EntityRelationshipRepository(self._session, org_id).list_for_source(definition.id)
        return _ResolvedLink(
            link=link, form=form, definition=definition, fields=fields, root_rels=root_rels
        )

    async def load(self, raw_token: str) -> PublicFormRead:
        resolved = await self._resolve(raw_token)
        config = FormConfig.model_validate(resolved.form.config or {})
        public_fields = _public_fields(config.fields, resolved.fields)

        # Prefill the exposed root fields from the target record.
        root_record: dict[str, Any] | None = None
        values: dict[str, Any] = {}
        status = resolved.link.status
        if resolved.link.target_record_id is not None:
            root_record = await self._root_repo(resolved).get(resolved.link.target_record_id)
            if root_record is not None:
                exposed = {f.slug for f in public_fields}
                values = {k: v for k, v in root_record.items() if k in exposed}

        sections = await self._load_sections(resolved, config, root_record)

        if unusable_reason(resolved.link, datetime.now(UTC)) is not None and status == "pending":
            status = "expired"
        return PublicFormRead(
            form_name=resolved.form.name,
            description=resolved.form.description,
            fields=public_fields,
            values=values,
            sections=sections,
            status=status,
        )

    async def submit(self, raw_token: str, payload: PublicFormSubmit) -> None:
        resolved = await self._resolve(raw_token)
        reason = unusable_reason(resolved.link, datetime.now(UTC))
        if reason is not None:
            raise FormLinkError(reason)
        if resolved.link.target_record_id is None:
            raise FormValidationError("link has no target record")

        # Single-use, race-safe: atomically flip pending -> submitted. Two
        # concurrent submissions of the same token both resolve the link as
        # "pending", but this conditional UPDATE serialises on the row lock — the
        # loser sees 0 rows (already submitted) and is rejected, so only ONE
        # submission commits its record writes.
        claimed = (
            await self._session.execute(
                text(
                    "UPDATE form_links SET status='submitted', submitted_at=now() "
                    "WHERE id=:id AND status='pending' RETURNING id"
                ),
                {"id": resolved.link.id},
            )
        ).first()
        if claimed is None:
            raise FormLinkError("This form has already been submitted.")
        root_id = resolved.link.target_record_id

        config = FormConfig.model_validate(resolved.form.config or {})
        exposed = {f.slug for f in _public_fields(config.fields, resolved.fields)}
        # Only accept values for fields the form actually exposes (defends the
        # entity against a crafted submission writing unrelated columns).
        clean = {k: v for k, v in payload.values.items() if k in exposed}

        try:
            updated = await self._root_repo(resolved).update(root_id, clean)
            if updated is None:
                raise FormNotFoundError("target record not found")
            await self._submit_sections(resolved, config, root_id, updated, payload.sections)
        except EntityRecordError as exc:
            raise FormValidationError(str(exc)) from exc

        resolved.link.status = "submitted"
        resolved.link.submitted_at = datetime.now(UTC)
        await self._session.flush()

    # ---- sections -------------------------------------------------------- #
    async def _section_meta(self, org_id: uuid.UUID, section: FormSectionConfig) -> _SectionMeta | None:
        rel = await EntityRelationshipRepository(self._session, org_id).get(section.relationship_id)
        if rel is None:
            return None  # relationship was deleted — skip the section, don't crash
        is_table = section.mode == "table"
        # 1:M table: the FK is on the child (rel.source); 1:1: on the root (rel.target is the related entity).
        related_id = rel.source_definition_id if is_table else rel.target_definition_id
        related_def = await EntityDefinitionRepository(self._session, org_id).get(related_id)
        if related_def is None:
            return None
        related_fields = await EntityFieldRepository(self._session, org_id).list_for_definition(related_id)
        return _SectionMeta(
            rel=rel,
            related_def=related_def,
            related_fields=related_fields,
            public_fields=_public_fields(section.fields, related_fields),
            is_table=is_table,
            mode=section.mode,
        )

    async def _load_sections(
        self, resolved: _ResolvedLink, config: FormConfig, root_record: dict[str, Any] | None
    ) -> list[PublicFormSection]:
        org_id = resolved.link.org_id
        out: list[PublicFormSection] = []
        for section in config.sections:
            meta = await self._section_meta(org_id, section)
            if meta is None:
                continue
            rows: list[dict[str, Any]] = []
            values: dict[str, Any] = {}
            if meta.is_table and resolved.link.target_record_id is not None:
                repo = await self._repo_for_def(org_id, meta.related_def)
                items, _ = await repo.list(
                    filters={meta.rel.slug: resolved.link.target_record_id}, limit=MAX_SECTION_ROWS
                )
                keep = meta.exposed_slugs | {"id"}
                rows = [{k: v for k, v in item.items() if k in keep} for item in items]
            elif not meta.is_table and root_record is not None:
                linked_id = root_record.get(meta.rel.slug)
                if linked_id is not None:
                    repo = await self._repo_for_def(org_id, meta.related_def)
                    linked = await repo.get(uuid.UUID(str(linked_id)))
                    if linked is not None:
                        values = {k: v for k, v in linked.items() if k in meta.exposed_slugs}
            out.append(
                PublicFormSection(
                    key=str(meta.rel.id),
                    label=section.label or meta.related_def.name,
                    mode=meta.mode,
                    entity_name=meta.related_def.name,
                    fields=meta.public_fields,
                    rows=rows,
                    values=values,
                )
            )
        return out

    async def _submit_sections(
        self,
        resolved: _ResolvedLink,
        config: FormConfig,
        root_id: uuid.UUID,
        root_record: dict[str, Any],
        submitted: dict[str, Any],
    ) -> None:
        org_id = resolved.link.org_id
        for section in config.sections:
            meta = await self._section_meta(org_id, section)
            if meta is None:
                continue
            data = submitted.get(str(meta.rel.id)) or {}
            repo = await self._repo_for_def(org_id, meta.related_def)
            if meta.is_table:
                await self._write_table_section(repo, meta, root_id, data.get("rows") or [])
            else:
                await self._write_single_section(
                    repo, meta, resolved, root_id, root_record, data.get("values") or {}
                )

    async def _write_table_section(
        self,
        repo: DynamicEntityRepository,
        meta: _SectionMeta,
        root_id: uuid.UUID,
        rows: list[dict[str, Any]],
    ) -> None:
        if len(rows) > MAX_SECTION_ROWS:
            raise FormValidationError(f"too many rows (max {MAX_SECTION_ROWS})")
        for row in rows:
            clean = {k: v for k, v in row.items() if k in meta.exposed_slugs}
            row_id = row.get("id")
            if row_id:
                # Update only rows that already belong to this root (ownership check).
                existing = await repo.get(uuid.UUID(str(row_id)))
                if existing is None or str(existing.get(meta.rel.slug)) != str(root_id):
                    continue
                await repo.update(uuid.UUID(str(row_id)), clean)
            else:
                await repo.create({meta.rel.slug: root_id, **clean})

    async def _write_single_section(
        self,
        repo: DynamicEntityRepository,
        meta: _SectionMeta,
        resolved: _ResolvedLink,
        root_id: uuid.UUID,
        root_record: dict[str, Any],
        raw_values: dict[str, Any],
    ) -> None:
        clean = {k: v for k, v in raw_values.items() if k in meta.exposed_slugs}
        if not clean:
            return
        linked_id = root_record.get(meta.rel.slug)
        if linked_id is not None:
            await repo.update(uuid.UUID(str(linked_id)), clean)
            return
        # No linked record yet: create one and point the root's FK at it.
        created = await repo.create(clean)
        await self._root_repo(resolved).update(root_id, {meta.rel.slug: created["id"]})

    # ---- repo builders --------------------------------------------------- #
    def _root_repo(self, resolved: _ResolvedLink) -> DynamicEntityRepository:
        # Includes the root's to-one relationships so 1:1 FK slugs resolve, and an
        # outbox writer so the update emits a workflow event like any other write.
        return DynamicEntityRepository(
            self._session,
            resolved.link.org_id,
            resolved.definition,
            resolved.fields,
            resolved.root_rels,
            outbox=OutboxWriter(self._session),
            outbox_source="form",
        )

    async def _repo_for_def(
        self, org_id: uuid.UUID, definition: EntityDefinition
    ) -> DynamicEntityRepository:
        fields = await EntityFieldRepository(self._session, org_id).list_for_definition(definition.id)
        rels = await EntityRelationshipRepository(self._session, org_id).list_for_source(definition.id)
        return DynamicEntityRepository(
            self._session, org_id, definition, fields, rels,
            outbox=OutboxWriter(self._session), outbox_source="form",
        )
