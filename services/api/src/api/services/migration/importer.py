"""Rebuild an exported bundle into the target org.

Everything runs on the privileged session in dependency order (see
``RESOURCE_ORDER``), maintaining an ``IdMap`` so every cross-reference (entity
ids, relationship ids inside form/view configs and workflow graphs, record FKs,
folder parents, document folders/tags) is rewritten to the newly-created rows.

Collision handling is per-resource, driven by ``CollisionStrategy``:

* ``skip``      — keep the existing row; map references onto it.
* ``overwrite`` — update the existing row in place (partial for entities: new
                  optional fields/relationships are added, existing ones are
                  never dropped or retyped).
* ``rename``    — create a suffixed copy alongside the existing row.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.agent import Agent
from api.models.mcp_server import McpServer
from api.repositories.agent import AgentRepository
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.document import DocumentRepository
from api.repositories.mcp_server import McpServerRepository
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError
from api.repositories.folder import FolderRepository
from api.repositories.tag import TagRepository
from api.repositories.workflow import (
    WorkflowConnectionRepository,
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
)
from api.schemas.aggregate import AggregateQuery
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from api.schemas.form import FormConfig, FormCreate, FormUpdate
from api.schemas.report import ReportCreate, ReportUpdate, Visualization
from api.schemas.view import ViewCreate, ViewUpdate
from api.services.entity_service import EntityError, EntityService
from api.services.folder_service import build_dot_path, compute_folder_masks
from api.services.form_service import FormError, FormService
from api.services.migration.bundle import (
    CollisionStrategy,
    GeneratedSecret,
    IdMap,
    ImportSummary,
    Selection,
    filter_resources,
    remap_refs,
    suffix_name,
    suffix_slug,
)
from api.services.report_service import ReportService
from api.services.view_service import ViewService
from api.services.workflow.service import WorkflowService

logger = logging.getLogger(__name__)


class MigrationImporter:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, settings: Settings) -> None:
        self._session = session
        self._org_id = org_id
        self._settings = settings
        self._ids = IdMap()
        # Documents whose ingest must be enqueued AFTER the import transaction
        # commits (so the worker can read the row), mirroring the create route.
        self._pending_ingests: list[Any] = []

    async def import_bundle(
        self,
        bundle: dict[str, Any],
        strategy: CollisionStrategy,
        *,
        dry_run: bool = False,
        selection: Selection | None = None,
    ) -> ImportSummary:
        summary = ImportSummary(strategy=strategy, dry_run=dry_run)
        # Narrow the bundle to only the objects the caller chose to import.
        resources = filter_resources(bundle.get("resources") or {}, selection)

        await self._import_tags(resources.get("tags") or [], strategy, summary)
        await self._import_entities(resources.get("entities") or [], strategy, summary)
        await self._import_connections(resources.get("connections") or [], strategy, summary)
        await self._import_folders(resources.get("folders") or [], strategy, summary)
        await self._import_workflows(resources.get("workflows") or [], strategy, summary)
        await self._import_inbound_endpoints(resources.get("inbound_endpoints") or [], strategy, summary)
        # Reports before forms/views: a form or view can embed a saved report
        # (ReportElement.report_id), so the reports id-map must be populated first
        # for those configs to remap the reference.
        await self._import_reports(resources.get("reports") or [], strategy, summary)
        await self._import_forms(resources.get("forms") or [], strategy, summary)
        await self._import_views(resources.get("views") or [], strategy, summary)
        # MCP servers before agents (agents reference them); both after workflows so
        # the workflow_allowlist ids remap.
        await self._import_mcp_servers(resources.get("mcp_servers") or [], strategy, summary)
        await self._import_agents(resources.get("agents") or [], strategy, summary)
        await self._import_records(resources.get("entities") or [], resources.get("records") or [], summary)
        await self._import_documents(resources.get("documents") or [], strategy, summary, dry_run=dry_run)
        return summary

    # ------------------------------------------------------------------ #
    # Lineage-aware matching (durable cross-environment identity)
    # ------------------------------------------------------------------ #
    # An incoming object carries a stable ``lineage_id`` (== its own id in the
    # origin env, or a stamped value if it was itself promoted). We match it to an
    # existing target row by lineage FIRST — so a promotion re-targets the same row
    # even after the object was renamed — and fall back to the natural key (slug /
    # name) only when no lineage match exists. On that first natural-key-only
    # promotion we *adopt* the incoming lineage onto the matched row so subsequent
    # promotions are lineage-linked. v1 bundles carry no ``lineage_id`` and so keep
    # the original natural-key behaviour unchanged.
    @staticmethod
    def _row_lineage(row: Any) -> str:
        """A target row's durable identity: its stamped ``lineage_id`` or its id."""
        return str(getattr(row, "lineage_id", None) or row.id)

    def _match_lineage(self, incoming: dict, existing_rows: list[Any], natural_found: Any) -> tuple[Any, bool]:
        """Return ``(matched_row, matched_by_lineage)``.

        Lineage match wins over ``natural_found``; when the bundle has no lineage
        (v1) or nothing matches, ``natural_found`` is returned unchanged.
        """
        incoming_lineage = incoming.get("lineage_id")
        if incoming_lineage:
            for row in existing_rows:
                if self._row_lineage(row) == incoming_lineage:
                    return row, True
        return natural_found, False

    def _stamp_new(self, row: Any, incoming: dict) -> None:
        """Stamp a freshly-created row with the origin's lineage so a later
        promotion re-targets it. No-op for v1 bundles (no lineage) and for a
        self-origin value that already equals the new row's own id."""
        lin = incoming.get("lineage_id")
        if lin and lin != str(row.id):
            row.lineage_id = uuid.UUID(lin)

    def _adopt_lineage(self, row: Any, incoming: dict, *, by_lineage: bool) -> None:
        """On a natural-key match during the first lineage-aware promotion, stamp
        the existing row so future promotions link by lineage. Skipped when the row
        was already found *by* lineage, is itself the origin, or already stamped."""
        if by_lineage:
            return
        lin = incoming.get("lineage_id")
        if lin and getattr(row, "lineage_id", None) is None and lin != str(row.id):
            row.lineage_id = uuid.UUID(lin)

    # ------------------------------------------------------------------ #
    # Tags
    # ------------------------------------------------------------------ #
    async def _import_tags(self, tags: list[dict], strategy: CollisionStrategy, summary: ImportSummary) -> None:
        repo = TagRepository(self._session, self._org_id)
        existing, _ = await repo.list_all(limit=10_000)
        by_name = {t.name: t for t in existing}
        out = summary.outcome("tags")
        for tag in tags:
            name = tag.get("name")
            if not name:
                continue
            found, by_lineage = self._match_lineage(tag, existing, by_name.get(name))
            if found is not None:
                # Tags are pure labels — a same-name tag IS the same tag; always
                # reuse it regardless of strategy (rename would fragment the vocab).
                self._adopt_lineage(found, tag, by_lineage=by_lineage)
                self._ids.put("tags", tag["id"], found.id)
                out.record("skipped")
                continue
            created = await repo.create(name=name)
            self._stamp_new(created, tag)
            existing.append(created)
            by_name[name] = created
            self._ids.put("tags", tag["id"], created.id)
            out.record("created")

    # ------------------------------------------------------------------ #
    # Entities (two passes: definitions+fields, then relationships)
    # ------------------------------------------------------------------ #
    async def _import_entities(
        self, entities: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        defs_repo = EntityDefinitionRepository(self._session, self._org_id)
        fields_repo = EntityFieldRepository(self._session, self._org_id)
        rels_repo = EntityRelationshipRepository(self._session, self._org_id)
        service = EntityService(self._session, self._org_id)
        out = summary.outcome("entities")
        existing_defs = (await defs_repo.list_all(limit=1000))[0]
        existing_slugs = {d.slug for d in existing_defs}
        defs_by_slug = {d.slug: d for d in existing_defs}

        # Pass 1: definitions + scalar fields.
        for ent in entities:
            slug = ent["slug"]
            found, by_lineage = self._match_lineage(ent, existing_defs, defs_by_slug.get(slug))
            try:
                if found is not None and strategy is CollisionStrategy.SKIP:
                    self._adopt_lineage(found, ent, by_lineage=by_lineage)
                    self._ids.put("entities", ent["id"], found.id)
                    out.record("skipped")
                    continue
                if found is not None and strategy is CollisionStrategy.OVERWRITE:
                    await defs_repo.update(found, name=ent["name"], description=ent.get("description"))
                    await self._add_missing_fields(service, fields_repo, found.id, ent["fields"], summary)
                    self._adopt_lineage(found, ent, by_lineage=by_lineage)
                    self._ids.put("entities", ent["id"], found.id)
                    out.record("overwritten")
                    continue
                # create (fresh, or rename-to-avoid-collision)
                new_slug = slug
                new_name = ent["name"]
                if found is not None:  # RENAME
                    new_slug = suffix_slug(slug, existing_slugs, sep="_")
                    new_name = suffix_name(ent["name"], set())
                existing_slugs.add(new_slug)
                created = await service.create_definition(
                    EntityDefinitionCreate(
                        name=new_name,
                        slug=new_slug,
                        description=ent.get("description"),
                        fields=[self._field_create(f) for f in ent["fields"]],
                    )
                )
                # A genuine create adopts the origin's lineage; a rename-fork is a
                # deliberate new object, so it stays self-origin (unstamped).
                if found is None:
                    self._stamp_new(created, ent)
                existing_defs.append(created)
                defs_by_slug[created.slug] = created
                self._ids.put("entities", ent["id"], created.id)
                out.record("renamed" if found is not None else "created")
            except (EntityError, ValueError) as exc:
                out.record("failed")
                summary.errors.append(f"entity {slug!r}: {exc}")

        # Pass 2: relationships (all target definitions now exist + are mapped).
        for ent in entities:
            new_source_id = self._ids.get("entities", ent["id"])
            if new_source_id is None:
                continue
            existing_rel_slugs = {
                r.slug for r in await rels_repo.list_for_source(uuid.UUID(new_source_id))
            }
            for rel in ent.get("relationships") or []:
                new_target_id = self._ids.get("entities", rel["target_definition_id"])
                if new_target_id is None:
                    summary.warnings.append(
                        f"relationship {rel['slug']!r} on {ent['slug']!r}: target entity not in bundle; skipped"
                    )
                    continue
                # Existing relationship with the same slug → map onto it, don't recreate.
                if rel["slug"] in existing_rel_slugs:
                    match = next(
                        r for r in await rels_repo.list_for_source(uuid.UUID(new_source_id))
                        if r.slug == rel["slug"]
                    )
                    self._ids.put("relationships", rel["id"], match.id)
                    continue
                try:
                    created = await service.create_relationship(
                        uuid.UUID(new_source_id),
                        EntityRelationshipCreate(
                            name=rel["name"],
                            slug=rel["slug"],
                            cardinality=rel["cardinality"],
                            target_definition_id=uuid.UUID(new_target_id),
                            on_delete=rel.get("on_delete", "SET NULL"),
                            is_required=rel.get("is_required", False),
                        ),
                    )
                    existing_rel_slugs.add(rel["slug"])
                    self._ids.put("relationships", rel["id"], created.id)
                except (EntityError, ValueError) as exc:
                    summary.warnings.append(f"relationship {rel['slug']!r} on {ent['slug']!r}: {exc}")

    async def _add_missing_fields(
        self,
        service: EntityService,
        fields_repo: EntityFieldRepository,
        definition_id: uuid.UUID,
        fields: list[dict],
        summary: ImportSummary,
    ) -> None:
        have = {f.slug for f in await fields_repo.list_for_definition(definition_id)}
        for f in fields:
            if f["slug"] in have:
                continue
            body = self._field_create(f)
            if body.is_required:
                summary.warnings.append(
                    f"field {f['slug']!r} added to existing entity as OPTIONAL (was required)"
                )
                body = body.model_copy(update={"is_required": False})
            try:
                await service.add_field(definition_id, body)
            except (EntityError, ValueError) as exc:
                summary.warnings.append(f"field {f['slug']!r}: {exc}")

    @staticmethod
    def _field_create(f: dict) -> EntityFieldCreate:
        return EntityFieldCreate(
            name=f["name"],
            slug=f["slug"],
            field_type=f["field_type"],
            picklist_options=f.get("picklist_options") or [],
            is_required=f.get("is_required", False),
            is_unique=f.get("is_unique", False),
            default_value=f.get("default_value"),
            order=f.get("order", 0),
        )

    # ------------------------------------------------------------------ #
    # Connections (secrets NEVER imported)
    # ------------------------------------------------------------------ #
    async def _import_connections(
        self, connections: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        repo = WorkflowConnectionRepository(self._session, self._org_id)
        existing_conns = await repo.list_all()
        existing = {c.name: c for c in existing_conns}
        out = summary.outcome("connections")
        for conn in connections:
            name = conn["name"]
            found, by_lineage = self._match_lineage(conn, existing_conns, existing.get(name))
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, conn, by_lineage=by_lineage)
                self._ids.put("connections", conn["id"], found.id)
                out.record("skipped")
                continue
            if found is not None and strategy is CollisionStrategy.OVERWRITE:
                await repo.update(
                    found,
                    base_url=conn.get("base_url"),
                    auth_type=conn.get("auth_type"),
                    config=conn.get("config") or {},
                )
                self._adopt_lineage(found, conn, by_lineage=by_lineage)
                self._ids.put("connections", conn["id"], found.id)
                out.record("overwritten")
                self._note_secret(conn, found.name, summary)
                continue
            new_name = name
            if found is not None:  # RENAME
                new_name = suffix_name(name, set(existing), max_len=120)
                summary.warnings.append(
                    f"connection renamed to {new_name!r}; workflows referencing {name!r} by name will not resolve"
                )
            created = await repo.create(
                name=new_name,
                kind=conn.get("kind", "http"),
                base_url=conn.get("base_url"),
                auth_type=conn.get("auth_type", "none"),
                secret_encrypted=None,
                config=conn.get("config") or {},
            )
            if found is None:
                self._stamp_new(created, conn)
            existing_conns.append(created)
            existing[new_name] = created
            self._ids.put("connections", conn["id"], created.id)
            out.record("renamed" if found is not None else "created")
            self._note_secret(conn, new_name, summary)

    @staticmethod
    def _note_secret(conn: dict, name: str, summary: ImportSummary) -> None:
        if conn.get("has_secret") and conn.get("auth_type", "none") != "none":
            summary.warnings.append(f"connection {name!r} needs its secret re-entered (not exported)")

    # ------------------------------------------------------------------ #
    # Folders (parents before children — bundle is dot_path ordered)
    # ------------------------------------------------------------------ #
    async def _import_folders(
        self, folders: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        repo = FolderRepository(self._session, self._org_id)
        out = summary.outcome("folders")
        # Lineage index over ALL existing folders (matching is normally by
        # (parent, name), but lineage lets a promotion re-target a renamed/moved folder).
        all_folders, _ = await repo.list_visible_to_masks(None)
        # Order shallowest-first so a parent is always created before its child.
        ordered = sorted(folders, key=lambda f: (f.get("dot_path") or "").count("."))
        for folder in ordered:
            parent_new = self._ids.get("folders", folder["parent_id"]) if folder.get("parent_id") else None
            parent_id = uuid.UUID(parent_new) if parent_new else None
            name = folder["name"]
            # Collision key = (parent, name), matching the DB unique constraint.
            sibling_rows = await repo.list_children(parent_id)
            siblings = {f.name for f in sibling_rows}
            natural = next((f for f in sibling_rows if f.name == name), None)
            found, by_lineage = self._match_lineage(folder, all_folders, natural)
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, folder, by_lineage=by_lineage)
                self._ids.put("folders", folder["id"], found.id)
                out.record("skipped")
                continue
            new_name = name
            action = "created"
            if found is not None and strategy is CollisionStrategy.OVERWRITE:
                # Folders carry no body — "overwrite" degrades to reuse the existing folder.
                self._adopt_lineage(found, folder, by_lineage=by_lineage)
                self._ids.put("folders", folder["id"], found.id)
                out.record("overwritten")
                continue
            if found is not None:  # RENAME (fork)
                new_name = suffix_name(name, siblings, max_len=255)
                action = "renamed"
            dot_path = await build_dot_path(self._session, self._org_id, new_name, parent_id)
            view_masks, contrib_masks = await compute_folder_masks(
                self._session,
                self._org_id,
                folder.get("viewer_permissions_config"),
                folder.get("contributor_permissions_config"),
            )
            created = await repo.create(
                name=new_name,
                parent_id=parent_id,
                description=folder.get("description"),
                viewer_permissions_config=folder.get("viewer_permissions_config"),
                contributor_permissions_config=folder.get("contributor_permissions_config"),
                view_permission_masks=view_masks,
                contributor_permission_masks=contrib_masks,
                dot_path=dot_path,
            )
            if found is None:
                self._stamp_new(created, folder)
            all_folders.append(created)
            self._ids.put("folders", folder["id"], created.id)
            out.record(action)

    # ------------------------------------------------------------------ #
    # Workflows (create + publish active version)
    # ------------------------------------------------------------------ #
    async def _import_workflows(
        self, workflows: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        repo = WorkflowRepository(self._session, self._org_id)
        service = WorkflowService(self._session, self._org_id)
        existing_wfs = await repo.list_all()
        existing = {w.name: w for w in existing_wfs}
        out = summary.outcome("workflows")
        for wf in workflows:
            name = wf["name"]
            found, by_lineage = self._match_lineage(wf, existing_wfs, existing.get(name))
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, wf, by_lineage=by_lineage)
                self._ids.put("workflows", wf["id"], found.id)
                out.record("skipped")
                continue
            old_entity = wf.get("entity_definition_id")
            entity_new = self._ids.get("entities", old_entity) if old_entity else None
            entity_id = uuid.UUID(entity_new) if entity_new else None
            definition = self._pick_definition(wf)
            remapped_def = remap_refs(definition, self._ids, summary.warnings) if definition else None
            try:
                if found is not None and strategy is CollisionStrategy.OVERWRITE:
                    target = found
                    action = "overwritten"
                    self._adopt_lineage(found, wf, by_lineage=by_lineage)
                else:
                    new_name = name if found is None else suffix_name(name, set(existing))
                    action = "created" if found is None else "renamed"
                    target = await service.create_workflow(
                        name=new_name, entity_definition_id=entity_id, description=wf.get("description")
                    )
                    if found is None:
                        self._stamp_new(target, wf)
                    existing[new_name] = target
                    existing_wfs.append(target)
                self._ids.put("workflows", wf["id"], target.id)
                if remapped_def is not None:
                    version = await service.save_draft(target.id, remapped_def)
                    if self._active_is_published(wf):
                        await service.publish(target.id, version.id)
                await repo.update(
                    target,
                    enabled=wf.get("enabled", False),
                    run_permission=self._sanitize_run_permission(wf.get("run_permission"), summary),
                )
                out.record(action)
            except Exception as exc:  # noqa: BLE001 - one bad workflow must not abort the import
                out.record("failed")
                summary.errors.append(f"workflow {name!r}: {exc}")

    @staticmethod
    def _pick_definition(wf: dict) -> dict | None:
        """The graph to import: the active published version if there is one,
        else the highest-numbered version (imported as an unpublished draft)."""
        versions = wf.get("versions") or []
        if not versions:
            return None
        active = wf.get("active_version_number")
        for v in versions:
            if v.get("version_number") == active:
                return v.get("definition") or {}
        return max(versions, key=lambda v: v.get("version_number", 0)).get("definition") or {}

    @staticmethod
    def _active_is_published(wf: dict) -> bool:
        return wf.get("active_version_number") is not None

    @staticmethod
    def _sanitize_run_permission(rp: dict | None, summary: ImportSummary) -> dict | None:
        """Role/group ids reference rows we don't migrate; drop them so the run
        gate falls back to org-admin instead of pointing at dead ids."""
        if not rp:
            return None
        if rp.get("role_ids") or rp.get("group_ids"):
            summary.warnings.append(
                "a workflow's run-permission roles/groups were reset to org-admin (roles/groups are not migrated)"
            )
            return {"mode": "org_admin", "role_ids": [], "group_ids": []}
        return rp

    # ------------------------------------------------------------------ #
    # Inbound endpoints (token + signing secret regenerated)
    # ------------------------------------------------------------------ #
    async def _import_inbound_endpoints(
        self, endpoints: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        import secrets

        from api.services.crypto import encrypt_secret
        from api.services.workflow.inbound import hash_token
        from api.services.workflow.webhook_signing import SIGNATURE_HEADER

        repo = WorkflowInboundEndpointRepository(self._session, self._org_id)
        existing_eps = await repo.list_all()
        existing = {e.name for e in existing_eps}
        out = summary.outcome("inbound_endpoints")
        org_key = self._settings.org_encryption_key.get_secret_value()
        for ep in endpoints:
            name = ep["name"]
            natural = next((e for e in existing_eps if e.name == name), None)
            found, by_lineage = self._match_lineage(ep, existing_eps, natural)
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, ep, by_lineage=by_lineage)
                out.record("skipped")
                continue
            workflow_new = self._ids.get("workflows", ep["workflow_id"])
            if workflow_new is None:
                out.record("failed")
                summary.errors.append(f"inbound endpoint {name!r}: workflow not in bundle")
                continue
            collision = found is not None
            new_name = name if not collision else suffix_name(name, existing, max_len=120)
            out_action = "renamed" if collision else "created"
            token = secrets.token_urlsafe(32)
            signing_secret = "whsec_" + secrets.token_urlsafe(32)
            created_ep = await repo.create(
                name=new_name,
                workflow_id=uuid.UUID(workflow_new),
                token_hash=hash_token(token),
                signing_secret_encrypted=encrypt_secret(signing_secret, org_key),
            )
            if found is None and created_ep is not None:
                self._stamp_new(created_ep, ep)
                existing_eps.append(created_ep)
            existing.add(new_name)
            url = f"{self._settings.public_base_url.rstrip('/')}/api/inbound/{token}"
            summary.generated_secrets.append(
                GeneratedSecret(
                    kind="inbound_endpoint",
                    name=new_name,
                    token=token,
                    url=url,
                    signing_secret=signing_secret,
                    signature_header=SIGNATURE_HEADER,
                )
            )
            out.record(out_action)

    # ------------------------------------------------------------------ #
    # Forms
    # ------------------------------------------------------------------ #
    async def _import_forms(
        self, forms: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        service = FormService(self._session, self._org_id)
        existing_forms = await service.list_forms()
        existing = {f.slug: f for f in existing_forms}
        out = summary.outcome("forms")
        for form in forms:
            slug = form["slug"]
            found, by_lineage = self._match_lineage(form, existing_forms, existing.get(slug))
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, form, by_lineage=by_lineage)
                self._ids.put("forms", form["id"], found.id)
                out.record("skipped")
                continue
            entity_new = self._ids.get("entities", form["entity_definition_id"])
            if entity_new is None:
                out.record("failed")
                summary.errors.append(f"form {slug!r}: entity not in bundle")
                continue
            config = self._config(form, summary)
            try:
                if found is not None and strategy is CollisionStrategy.OVERWRITE:
                    updated = await service.update_form(
                        found.id, FormUpdate(name=form["name"], description=form.get("description"), config=config)
                    )
                    self._adopt_lineage(found, form, by_lineage=by_lineage)
                    self._ids.put("forms", form["id"], updated.id)
                    out.record("overwritten")
                    continue
                new_slug = slug if found is None else suffix_slug(slug, set(existing))
                new_name = form["name"] if found is None else suffix_name(form["name"], set())
                created = await service.create_form(
                    FormCreate(
                        name=new_name,
                        slug=new_slug,
                        entity_definition_id=uuid.UUID(entity_new),
                        description=form.get("description"),
                        config=config,
                    )
                )
                if found is None:
                    self._stamp_new(created, form)
                existing_forms.append(created)
                existing[new_slug] = created
                self._ids.put("forms", form["id"], created.id)
                out.record("created" if found is None else "renamed")
            except FormError as exc:
                out.record("failed")
                summary.errors.append(f"form {slug!r}: {exc}")

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #
    async def _import_reports(
        self, reports: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        service = ReportService(self._session, self._org_id)
        existing_reports = await service.list_reports()
        existing = {r.slug: r for r in existing_reports}
        out = summary.outcome("reports")
        for report in reports:
            slug = report["slug"]
            found, by_lineage = self._match_lineage(report, existing_reports, existing.get(slug))
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, report, by_lineage=by_lineage)
                self._ids.put("reports", report["id"], found.id)
                out.record("skipped")
                continue
            # A report always binds to an entity; the entity must be in the bundle.
            entity_new = self._ids.get("entities", report["entity_definition_id"])
            if entity_new is None:
                out.record("failed")
                summary.errors.append(f"report {slug!r}: entity not in bundle")
                continue
            is_active = bool(report.get("is_active", True))
            try:
                # query/viz hold field slugs (stable per entity), not ids — no remap.
                # Validate inside the try so a forward-version/hand-edited bundle
                # records one `failed` rather than aborting the whole import.
                query = AggregateQuery.model_validate(report.get("query") or {})
                viz = Visualization.model_validate(report.get("viz") or {})
                if found is not None and strategy is CollisionStrategy.OVERWRITE:
                    # ReportUpdate can't rebind the entity; overwriting a same-slug
                    # report bound to a DIFFERENT entity would validate the query
                    # against the wrong entity. Refuse rather than corrupt.
                    if str(found.entity_definition_id) != entity_new:
                        out.record("failed")
                        summary.errors.append(
                            f"report {slug!r}: existing report is bound to a different entity; not overwritten"
                        )
                        continue
                    updated = await service.update_report(
                        found.id,
                        ReportUpdate(
                            name=report["name"],
                            description=report.get("description"),
                            query=query,
                            viz=viz,
                            is_active=is_active,
                        ),
                    )
                    self._adopt_lineage(found, report, by_lineage=by_lineage)
                    self._ids.put("reports", report["id"], updated.id)
                    out.record("overwritten")
                    continue
                new_slug = slug if found is None else suffix_slug(slug, set(existing))
                new_name = report["name"] if found is None else suffix_name(report["name"], set())
                created = await service.create_report(
                    ReportCreate(
                        name=new_name,
                        slug=new_slug,
                        description=report.get("description"),
                        entity_definition_id=uuid.UUID(entity_new),
                        query=query,
                        viz=viz,
                        is_active=is_active,
                    )
                )
                if found is None:
                    self._stamp_new(created, report)
                existing_reports.append(created)
                existing[new_slug] = created
                self._ids.put("reports", report["id"], created.id)
                out.record("created" if found is None else "renamed")
            except (FormError, ValidationError) as exc:
                out.record("failed")
                summary.errors.append(f"report {slug!r}: {exc}")

    # ------------------------------------------------------------------ #
    # Views
    # ------------------------------------------------------------------ #
    async def _import_views(
        self, views: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        service = ViewService(self._session, self._org_id)
        existing_views = await service.list_views()
        existing = {v.slug: v for v in existing_views}
        out = summary.outcome("views")
        for view in views:
            slug = view["slug"]
            found, by_lineage = self._match_lineage(view, existing_views, existing.get(slug))
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, view, by_lineage=by_lineage)
                self._ids.put("views", view["id"], found.id)
                out.record("skipped")
                continue
            entity_new = (
                self._ids.get("entities", view["entity_definition_id"])
                if view.get("entity_definition_id")
                else None
            )
            if view.get("entity_definition_id") and entity_new is None:
                out.record("failed")
                summary.errors.append(f"view {slug!r}: entity not in bundle")
                continue
            config = self._config(view, summary)
            try:
                if found is not None and strategy is CollisionStrategy.OVERWRITE:
                    updated = await service.update_view(
                        found.id, ViewUpdate(name=view["name"], description=view.get("description"), config=config)
                    )
                    self._adopt_lineage(found, view, by_lineage=by_lineage)
                    self._ids.put("views", view["id"], updated.id)
                    out.record("overwritten")
                    continue
                new_slug = slug if found is None else suffix_slug(slug, set(existing))
                new_name = view["name"] if found is None else suffix_name(view["name"], set())
                created = await service.create_view(
                    ViewCreate(
                        name=new_name,
                        slug=new_slug,
                        description=view.get("description"),
                        entity_definition_id=uuid.UUID(entity_new) if entity_new else None,
                        config=config,
                    )
                )
                if found is None:
                    self._stamp_new(created, view)
                existing_views.append(created)
                existing[new_slug] = created
                self._ids.put("views", view["id"], created.id)
                out.record("created" if found is None else "renamed")
            except FormError as exc:
                out.record("failed")
                summary.errors.append(f"view {slug!r}: {exc}")

    def _config(self, resource: dict, summary: ImportSummary) -> FormConfig:
        """Remap embedded ids (relationship_id, form_id, workflow_id, …) then parse."""
        remapped = remap_refs(resource.get("config") or {}, self._ids, summary.warnings)
        return FormConfig.model_validate(remapped)

    # ------------------------------------------------------------------ #
    # MCP servers (secrets NEVER imported)
    # ------------------------------------------------------------------ #
    async def _import_mcp_servers(
        self, servers: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        repo = McpServerRepository(self._session, self._org_id)
        existing_srvs = await repo.list_all()
        existing = {s.name: s for s in existing_srvs}
        out = summary.outcome("mcp_servers")
        for srv in servers:
            name = srv["name"]
            found, by_lineage = self._match_lineage(srv, existing_srvs, existing.get(name))
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, srv, by_lineage=by_lineage)
                self._ids.put("mcp_servers", srv["id"], found.id)
                out.record("skipped")
                continue
            if found is not None and strategy is CollisionStrategy.OVERWRITE:
                found.description = srv.get("description")
                found.transport = srv.get("transport", "http")
                found.command = srv.get("command")
                found.url = srv.get("url")
                found.config = srv.get("config") or {}
                found.enabled = srv.get("enabled", True)
                self._adopt_lineage(found, srv, by_lineage=by_lineage)
                await repo.flush()
                self._ids.put("mcp_servers", srv["id"], found.id)
                out.record("overwritten")
                self._note_mcp_secret(srv, found.name, summary)
                continue
            new_name = name if found is None else suffix_name(name, set(existing), max_len=120)
            created = await repo.create(
                McpServer(
                    name=new_name,
                    description=srv.get("description"),
                    transport=srv.get("transport", "http"),
                    command=srv.get("command"),
                    url=srv.get("url"),
                    config=srv.get("config") or {},
                    secret_encrypted=None,
                    enabled=srv.get("enabled", True),
                )
            )
            if found is None:
                self._stamp_new(created, srv)
            existing_srvs.append(created)
            existing[new_name] = created
            self._ids.put("mcp_servers", srv["id"], created.id)
            out.record("created" if found is None else "renamed")
            self._note_mcp_secret(srv, new_name, summary)

    @staticmethod
    def _note_mcp_secret(srv: dict, name: str, summary: ImportSummary) -> None:
        if srv.get("has_secret"):
            summary.warnings.append(f"MCP server {name!r} needs its secret re-entered (not exported)")

    # ------------------------------------------------------------------ #
    # Agents (two passes: create + remap tool/mcp/workflow refs, then supervisors)
    # ------------------------------------------------------------------ #
    async def _import_agents(
        self, agents: list[dict], strategy: CollisionStrategy, summary: ImportSummary
    ) -> None:
        repo = AgentRepository(self._session, self._org_id)
        existing_agents = await repo.list_all()
        existing = {a.name: a for a in existing_agents}
        out = summary.outcome("agents")

        # Pass 1: create/find + remap mcp_server_ids + workflow_allowlist.
        for agent in agents:
            name = agent["name"]
            found, by_lineage = self._match_lineage(agent, existing_agents, existing.get(name))
            if found is not None and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, agent, by_lineage=by_lineage)
                self._ids.put("agents", agent["id"], found.id)
                out.record("skipped")
                continue
            mcp_ids = self._remap_id_list("mcp_servers", agent.get("mcp_server_ids"))
            wf_ids = self._remap_id_list("workflows", agent.get("workflow_allowlist"), summary, agent["name"])
            if found is not None and strategy is CollisionStrategy.OVERWRITE:
                self._apply_agent_fields(found, agent, mcp_ids, wf_ids)
                self._adopt_lineage(found, agent, by_lineage=by_lineage)
                await repo.flush()
                self._ids.put("agents", agent["id"], found.id)
                out.record("overwritten")
                continue
            new_name = name if found is None else suffix_slug(name, set(existing), sep="-")
            model = Agent(name=new_name, provider=agent["provider"], model=agent["model"])
            self._apply_agent_fields(model, agent, mcp_ids, wf_ids)
            if found is None:
                self._stamp_new(model, agent)
            created = await repo.create(model)
            existing_agents.append(created)
            existing[new_name] = created
            self._ids.put("agents", agent["id"], created.id)
            out.record("created" if found is None else "renamed")

        # Pass 2: supervisors (all agents now exist + are mapped).
        for agent in agents:
            new_id = self._ids.get("agents", agent["id"])
            if new_id is None or not agent.get("supervisor_id"):
                continue
            sup_new = self._ids.get("agents", agent["supervisor_id"])
            if sup_new is None:
                summary.warnings.append(f"agent {agent['name']!r}: supervisor not in bundle; left unassigned")
                continue
            row = await repo.get(uuid.UUID(new_id))
            if row is not None:
                row.supervisor_id = uuid.UUID(sup_new)
        await repo.flush()

    def _remap_id_list(
        self, namespace: str, old_ids: list | None, summary: ImportSummary | None = None, agent_name: str = ""
    ) -> list[str]:
        out: list[str] = []
        for old in old_ids or []:
            mapped = self._ids.get(namespace, old)
            if mapped is not None:
                out.append(mapped)
            elif summary is not None:
                summary.warnings.append(
                    f"agent {agent_name!r}: a {namespace} reference was not in the bundle and was dropped"
                )
        return out

    @staticmethod
    def _apply_agent_fields(model: Agent, agent: dict, mcp_ids: list[str], wf_ids: list[str]) -> None:
        model.display_name = agent.get("display_name")
        model.description = agent.get("description")
        model.kind = agent.get("kind", "operator")
        model.persona = agent.get("persona")
        model.provider = agent["provider"]
        model.model = agent["model"]
        model.params = agent.get("params") or {}
        model.avatar = agent.get("avatar")
        model.accent = agent.get("accent")
        model.enabled = agent.get("enabled", True)
        model.grants = agent.get("grants") or {}
        model.mcp_server_ids = mcp_ids
        model.workflow_allowlist = wf_ids

    # ------------------------------------------------------------------ #
    # Records (best-effort: dependency order + deferred FK second pass)
    # ------------------------------------------------------------------ #
    async def _import_records(
        self, entities: list[dict], record_sets: list[dict], summary: ImportSummary
    ) -> None:
        if not record_sets:
            return
        defs_repo = EntityDefinitionRepository(self._session, self._org_id)
        fields_repo = EntityFieldRepository(self._session, self._org_id)
        rels_repo = EntityRelationshipRepository(self._session, self._org_id)
        out = summary.outcome("records")

        by_slug = {e["slug"]: e for e in entities}
        # to-one relationship slug -> target entity slug, per source entity slug.
        rel_targets: dict[str, dict[str, str]] = {}
        for e in entities:
            m: dict[str, str] = {}
            for rel in e.get("relationships") or []:
                if rel.get("cardinality") != "many_to_many":
                    tslug = _target_slug(rel, by_slug)
                    if tslug is not None:
                        m[rel["slug"]] = tslug
            rel_targets[e["slug"]] = m

        ordered = _topo_order(record_sets, rel_targets)
        # Deferred: (entity_slug, new_record_id, fk_slug, target_entity_slug, old_target_id)
        deferred: list[tuple[str, str, str, str, str]] = []

        repos: dict[str, DynamicEntityRepository] = {}

        async def repo_for(entity_slug: str) -> DynamicEntityRepository | None:
            if entity_slug in repos:
                return repos[entity_slug]
            new_id = self._ids.get("entities", by_slug[entity_slug]["id"]) if entity_slug in by_slug else None
            if new_id is None:
                return None
            definition = await defs_repo.get(uuid.UUID(new_id))
            if definition is None:
                return None
            fields = await fields_repo.list_for_definition(definition.id)
            rels = await rels_repo.list_for_source(definition.id)
            repo = DynamicEntityRepository(self._session, self._org_id, definition, fields, rels)
            repos[entity_slug] = repo
            return repo

        for entity_slug in ordered:
            record_set = next((rs for rs in record_sets if rs["entity_slug"] == entity_slug), None)
            if record_set is None:
                continue
            repo = await repo_for(entity_slug)
            if repo is None:
                continue
            fk_slugs = rel_targets.get(entity_slug, {})
            for rec in record_set.get("records") or []:
                old_id = rec.get("id")
                payload: dict[str, Any] = {}
                for key, value in rec.items():
                    if key in ("id",) or value is None:
                        continue
                    if key in fk_slugs:
                        target_slug = fk_slugs[key]
                        mapped = self._ids.get(f"record:{target_slug}", value)
                        if mapped is not None:
                            payload[key] = mapped
                        elif old_id is not None:
                            deferred.append((entity_slug, old_id, key, target_slug, str(value)))
                        continue
                    payload[key] = value
                try:
                    created = await repo.create(payload)
                    if old_id is not None:
                        self._ids.put(f"record:{entity_slug}", old_id, created["id"])
                    out.record("created")
                except EntityRecordError as exc:
                    out.record("failed")
                    summary.warnings.append(f"record in {entity_slug!r}: {exc}")

        # Second pass: resolve FKs that pointed at not-yet-created records.
        for entity_slug, old_source_id, fk_slug, target_slug, old_target_id in deferred:
            new_source = self._ids.get(f"record:{entity_slug}", old_source_id)
            new_target = self._ids.get(f"record:{target_slug}", old_target_id)
            if new_source is None or new_target is None:
                continue
            repo = await repo_for(entity_slug)
            if repo is None:
                continue
            try:
                await repo.update(uuid.UUID(new_source), {fk_slug: new_target})
            except EntityRecordError as exc:
                summary.warnings.append(f"record FK {fk_slug!r} in {entity_slug!r}: {exc}")

    # ------------------------------------------------------------------ #
    # Documents (create + best-effort ingest dispatch)
    # ------------------------------------------------------------------ #
    async def _import_documents(
        self, documents: list[dict], strategy: CollisionStrategy, summary: ImportSummary, *, dry_run: bool
    ) -> None:
        if not documents:
            return
        doc_repo = DocumentRepository(self._session, self._org_id)
        tag_repo = TagRepository(self._session, self._org_id)
        out = summary.outcome("documents")

        existing_tags = {t.name: t for t in (await tag_repo.list_all(limit=10_000))[0]}
        # Existing (folder_id, title) keys for collision detection, plus the doc
        # rows themselves for lineage matching.
        existing_keys: set[tuple[str | None, str]] = set()
        existing_docs: list[Any] = []
        off = 0
        while True:
            docs, total = await doc_repo.list_for_folders(None, include_unfiled=True, offset=off, limit=200)
            for d in docs:
                existing_keys.add((str(d.folder_id) if d.folder_id else None, d.title))
                existing_docs.append(d)
            off += len(docs)
            if not docs or off >= total:
                break
        by_key = {(str(d.folder_id) if d.folder_id else None, d.title): d for d in existing_docs}

        for doc in documents:
            folder_new = self._ids.get("folders", doc["folder_id"]) if doc.get("folder_id") else None
            folder_id = uuid.UUID(folder_new) if folder_new else None
            title = doc["title"]
            key = (folder_new, title)
            found, by_lineage = self._match_lineage(doc, existing_docs, by_key.get(key))
            collision = found is not None
            if collision and strategy is CollisionStrategy.SKIP:
                self._adopt_lineage(found, doc, by_lineage=by_lineage)
                out.record("skipped")
                continue
            new_title = title
            action = "created"
            if collision and strategy is CollisionStrategy.RENAME:
                new_title = suffix_name(title, {t for (_, t) in existing_keys}, max_len=255)
                action = "renamed"
            elif collision and strategy is CollisionStrategy.OVERWRITE:
                # No stable natural key to update in place; create a fresh row and
                # let re-ingest supersede the stale copy's vectors.
                action = "overwritten"
            tag_ids = await self._resolve_tag_ids(doc.get("tag_names") or [], existing_tags, tag_repo)
            created = await doc_repo.create(
                title=new_title,
                text=doc.get("text"),
                description=doc.get("description"),
                folder_id=folder_id,
                use_knowledge_graph=doc.get("use_knowledge_graph"),
                metadata=doc.get("metadata") or {},
                tag_ids=tag_ids,
            )
            created.size_bytes = len(doc["text"].encode("utf-8")) if doc.get("text") else None
            if found is None:
                self._stamp_new(created, doc)
            existing_docs.append(created)
            existing_keys.add((folder_new, new_title))
            out.record(action)
            if not dry_run and created.text:
                self._pending_ingests.append(created)

    async def _resolve_tag_ids(
        self, names: list[str], existing: dict, tag_repo: TagRepository
    ) -> list[uuid.UUID]:
        ids: list[uuid.UUID] = []
        for name in names:
            tag = existing.get(name)
            if tag is None:
                tag = await tag_repo.create(name=name)
                existing[name] = tag
            ids.append(tag.id)
        return ids

    async def dispatch_pending_ingests(self, summary: ImportSummary) -> None:
        """Enqueue ingestion for imported documents. MUST be called by the router
        AFTER the import transaction has committed, so the worker can read each
        row (mirrors ``POST /documents``). Best-effort: a broker outage leaves the
        document PENDING for a later reconciliation sweep rather than failing."""
        if not self._pending_ingests:
            return
        from api.tasks.ingest import dispatch_ingest

        folder_repo = FolderRepository(self._session, self._org_id)
        for doc in self._pending_ingests:
            try:
                folder = await folder_repo.get(doc.folder_id) if doc.folder_id else None
                access_keys = await folder_repo.effective_view_masks(folder) if folder else []
                tags = [t.name for t in doc.tags]
                if doc.folder_id:
                    tags.append(f"folder:{doc.folder_id}")
                doc.celery_task_id = dispatch_ingest(
                    {
                        "document_id": str(doc.id),
                        "tenant_id": str(self._org_id),
                        "document_key": doc.document_key,
                        "title": doc.title,
                        "text": doc.text,
                        "tags": tags,
                        "access_keys": access_keys,
                        "use_knowledge_graph": doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True,
                        "metadata": doc.metadata_ or {},
                    }
                )
            except Exception:  # noqa: BLE001 - a broker outage must not fail the import
                logger.exception("import: ingest enqueue failed for document %s; left PENDING", doc.id)
                summary.warnings.append(f"document {doc.title!r} created but ingestion could not be queued")


def _target_slug(rel: dict, by_slug: dict) -> str | None:
    """Resolve a relationship's target entity *slug* from the bundle by matching
    ``target_definition_id`` against each exported entity's original id."""
    tid = rel.get("target_definition_id")
    for slug, ent in by_slug.items():
        if ent["id"] == tid:
            return slug
    return None


def _topo_order(record_sets: list[dict], rel_targets: dict[str, dict[str, str]]) -> list[str]:
    """Order entity slugs so a record's FK targets are imported first. Falls back
    to input order on a dependency cycle (the deferred second pass covers it)."""
    slugs = [rs["entity_slug"] for rs in record_sets]
    present = set(slugs)
    ordered: list[str] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(slug: str) -> None:
        if slug in done or slug not in present:
            return
        if slug in visiting:  # cycle — stop descending, deferred pass handles it
            return
        visiting.add(slug)
        for target in set(rel_targets.get(slug, {}).values()):
            visit(target)
        visiting.discard(slug)
        done.add(slug)
        ordered.append(slug)

    for slug in slugs:
        visit(slug)
    return ordered
