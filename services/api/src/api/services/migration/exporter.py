"""Serialize every authorable resource in an org into a portable JSON bundle.

Runs on the privileged session; every read is explicitly org-scoped by the
underlying repositories, so it does not rely on RLS being in effect.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.agent import AgentRepository
from api.repositories.document import DocumentRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.folder import FolderRepository
from api.repositories.mcp_server import McpServerRepository
from api.repositories.form import FormRepository
from api.repositories.report import ReportRepository
from api.repositories.tag import TagRepository
from api.repositories.view import ViewRepository
from api.repositories.workflow import (
    WorkflowConnectionRepository,
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
    WorkflowVersionRepository,
    json_safe,
)
from api.services import identifiers
from api.services.migration.bundle import (
    BUNDLE_FORMAT_VERSION,
    BUNDLE_KIND,
    Selection,
    filter_resources,
)

# Never export more than this many rows per entity/document set — a guard against
# an accidental multi-GB bundle. Truncation is surfaced as a warning in the bundle.
MAX_RECORDS_PER_ENTITY = 20_000
MAX_DOCUMENTS = 10_000
_PAGE = 200


class MigrationExporter:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def export(
        self,
        *,
        selection: Selection | None = None,
        include_records: bool = True,
        include_documents: bool = True,
    ) -> dict[str, Any]:
        """Serialize the org, optionally narrowed to ``selection`` (a per-type set
        of ids to include; a type absent from the selection includes all of it)."""
        warnings: list[str] = []
        sel = selection or {}
        # Pre-filter the heavy data reads by the selection so we don't materialize
        # rows/text that would only be discarded.
        record_slugs = set(sel["records"]) if "records" in sel else None
        doc_ids = set(sel["documents"]) if "documents" in sel else None

        entities = await self._export_entities()
        resources: dict[str, Any] = {
            "tags": await self._export_tags(),
            "entities": entities,
            "connections": await self._export_connections(),
            "folders": await self._export_folders(),
            "workflows": await self._export_workflows(),
            "inbound_endpoints": await self._export_inbound_endpoints(),
            "forms": await self._export_forms(),
            "reports": await self._export_reports(),
            "views": await self._export_views(),
            "mcp_servers": await self._export_mcp_servers(),
            "agents": await self._export_agents(),
            "records": await self._export_records(entities, warnings, record_slugs) if include_records else [],
            "documents": await self._export_documents(warnings, doc_ids) if include_documents else [],
        }
        # Trim the config-layer lists (and idempotently records/documents) to the
        # selection.
        resources = filter_resources(resources, selection)

        counts = {k: len(v) for k, v in resources.items()}
        counts["records"] = sum(len(e["records"]) for e in resources["records"])
        return {
            "kind": BUNDLE_KIND,
            "format_version": BUNDLE_FORMAT_VERSION,
            "source_org_id": str(self._org_id),
            "counts": counts,
            "warnings": warnings,
            "resources": resources,
        }

    async def manifest(self) -> dict[str, Any]:
        """A lightweight index of every selectable object in the org (ids + names,
        no record rows or document text) so the UI can offer search + checkboxes
        before an export."""
        entities = await self._export_entities()
        conns = await self._export_connections()
        folders = await self._export_folders()
        workflows = await self._export_workflows()
        endpoints = await self._export_inbound_endpoints()
        forms = await self._export_forms()
        reports = await self._export_reports()
        views = await self._export_views()
        tags = await self._export_tags()
        mcp_servers = await self._export_mcp_servers()
        agents = await self._export_agents()
        return {
            "tags": [{"id": t["id"], "name": t["name"]} for t in tags],
            "entities": [{"id": e["id"], "name": e["name"], "slug": e["slug"]} for e in entities],
            "connections": [{"id": c["id"], "name": c["name"]} for c in conns],
            "folders": [{"id": f["id"], "name": f["name"], "dot_path": f["dot_path"]} for f in folders],
            "workflows": [{"id": w["id"], "name": w["name"]} for w in workflows],
            "inbound_endpoints": [{"id": e["id"], "name": e["name"]} for e in endpoints],
            "forms": [{"id": f["id"], "name": f["name"], "slug": f["slug"]} for f in forms],
            "reports": [{"id": r["id"], "name": r["name"], "slug": r["slug"]} for r in reports],
            "views": [{"id": v["id"], "name": v["name"], "slug": v["slug"]} for v in views],
            "mcp_servers": [{"id": s["id"], "name": s["name"]} for s in mcp_servers],
            "agents": [{"id": a["id"], "name": a["name"]} for a in agents],
            "records": await self._record_counts(entities),
            "documents": await self._document_index(),
        }

    async def _record_counts(self, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Per-entity record tallies for the manifest (a cheap COUNT per physical
        table; entity counts per org are modest)."""
        defs_repo = EntityDefinitionRepository(self._session, self._org_id)
        out: list[dict[str, Any]] = []
        for ent in entities:
            definition = await defs_repo.get(uuid.UUID(ent["id"]))
            if definition is None:
                continue
            table = identifiers.quote(definition.physical_table)
            count = (
                await self._session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE org_id = :org"),  # noqa: S608 - table is a validated identifier
                    {"org": self._org_id},
                )
            ).scalar_one()
            out.append({"entity_slug": ent["slug"], "name": ent["name"], "count": int(count)})
        return out

    async def _document_index(self) -> list[dict[str, Any]]:
        repo = DocumentRepository(self._session, self._org_id)
        out: list[dict[str, Any]] = []
        offset = 0
        while len(out) < MAX_DOCUMENTS:
            docs, total = await repo.list_for_folders(None, include_unfiled=True, offset=offset, limit=_PAGE)
            if not docs:
                break
            out.extend({"id": str(d.id), "title": d.title} for d in docs)
            offset += len(docs)
            if offset >= total:
                break
        return out

    # ------------------------------------------------------------------ #
    # Config layer
    # ------------------------------------------------------------------ #
    async def _export_tags(self) -> list[dict[str, Any]]:
        tags, _ = await TagRepository(self._session, self._org_id).list_all(limit=10_000)
        return [{"id": str(t.id), "name": t.name} for t in tags]

    async def _export_entities(self) -> list[dict[str, Any]]:
        defs_repo = EntityDefinitionRepository(self._session, self._org_id)
        fields_repo = EntityFieldRepository(self._session, self._org_id)
        rels_repo = EntityRelationshipRepository(self._session, self._org_id)
        definitions, _ = await defs_repo.list_all(limit=1000)
        out: list[dict[str, Any]] = []
        for d in definitions:
            fields = await fields_repo.list_for_definition(d.id)
            rels = await rels_repo.list_for_source(d.id)
            out.append(
                {
                    "id": str(d.id),
                    "name": d.name,
                    "slug": d.slug,
                    "description": d.description,
                    "is_active": d.is_active,
                    "fields": [
                        {
                            "id": str(f.id),
                            "name": f.name,
                            "slug": f.slug,
                            "field_type": f.field_type,
                            "picklist_options": list(f.picklist_options or []),
                            "is_required": f.is_required,
                            "is_unique": f.is_unique,
                            "default_value": json_safe(f.default_value),
                            "order": f.order,
                        }
                        for f in fields
                    ],
                    "relationships": [
                        {
                            "id": str(r.id),
                            "name": r.name,
                            "slug": r.slug,
                            "cardinality": r.cardinality,
                            "on_delete": r.on_delete,
                            "is_required": r.is_required,
                            "target_definition_id": str(r.target_definition_id),
                        }
                        for r in rels
                    ],
                }
            )
        return out

    async def _export_connections(self) -> list[dict[str, Any]]:
        conns = await WorkflowConnectionRepository(self._session, self._org_id).list_all()
        return [
            {
                "id": str(c.id),
                "name": c.name,
                "kind": c.kind,
                "base_url": c.base_url,
                "auth_type": c.auth_type,
                "config": json_safe(c.config or {}),
                "has_secret": bool(c.secret_encrypted),  # value never exported
            }
            for c in conns
        ]

    async def _export_folders(self) -> list[dict[str, Any]]:
        folders, _ = await FolderRepository(self._session, self._org_id).list_visible_to_masks(None)
        # Sorted by dot_path already (list query orders by dot_path) → parents first.
        return [
            {
                "id": str(f.id),
                "name": f.name,
                "description": f.description,
                "dot_path": f.dot_path,
                "parent_id": str(f.parent_id) if f.parent_id else None,
                "order": f.order,
                "viewer_permissions_config": f.viewer_permissions_config,
                "contributor_permissions_config": f.contributor_permissions_config,
            }
            for f in folders
        ]

    async def _export_workflows(self) -> list[dict[str, Any]]:
        wf_repo = WorkflowRepository(self._session, self._org_id)
        ver_repo = WorkflowVersionRepository(self._session, self._org_id)
        workflows = await wf_repo.list_all()
        out: list[dict[str, Any]] = []
        for wf in workflows:
            versions = await ver_repo.list_for_workflow(wf.id)
            active_number = None
            for v in versions:
                if v.id == wf.active_version_id:
                    active_number = v.version_number
                    break
            out.append(
                {
                    "id": str(wf.id),
                    "name": wf.name,
                    "description": wf.description,
                    "entity_definition_id": str(wf.entity_definition_id) if wf.entity_definition_id else None,
                    "enabled": wf.enabled,
                    "run_permission": json_safe(wf.run_permission or {}),
                    "active_version_number": active_number,
                    "versions": [
                        {
                            "version_number": v.version_number,
                            "status": v.status,
                            "definition": json_safe(v.definition or {}),
                        }
                        for v in versions
                    ],
                }
            )
        return out

    async def _export_inbound_endpoints(self) -> list[dict[str, Any]]:
        items = await WorkflowInboundEndpointRepository(self._session, self._org_id).list_all()
        return [
            {
                "id": str(e.id),
                "name": e.name,
                "workflow_id": str(e.workflow_id),
                "enabled": e.enabled,
                "has_signing_secret": bool(e.signing_secret_encrypted),  # secret regenerated on import
            }
            for e in items
        ]

    async def _export_forms(self) -> list[dict[str, Any]]:
        forms = await FormRepository(self._session, self._org_id).list_all()
        return [
            {
                "id": str(f.id),
                "name": f.name,
                "slug": f.slug,
                "description": f.description,
                "entity_definition_id": str(f.entity_definition_id),
                "config": json_safe(f.config or {}),
                "is_active": f.is_active,
            }
            for f in forms
        ]

    async def _export_reports(self) -> list[dict[str, Any]]:
        reports = await ReportRepository(self._session, self._org_id).list_all()
        return [
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "description": r.description,
                "entity_definition_id": str(r.entity_definition_id),
                "query": json_safe(r.query or {}),
                "viz": json_safe(r.viz or {}),
                "is_active": r.is_active,
            }
            for r in reports
        ]

    async def _export_views(self) -> list[dict[str, Any]]:
        views = await ViewRepository(self._session, self._org_id).list_all()
        return [
            {
                "id": str(v.id),
                "name": v.name,
                "slug": v.slug,
                "description": v.description,
                "entity_definition_id": str(v.entity_definition_id) if v.entity_definition_id else None,
                "config": json_safe(v.config or {}),
                "is_active": v.is_active,
            }
            for v in views
        ]

    async def _export_mcp_servers(self) -> list[dict[str, Any]]:
        servers = await McpServerRepository(self._session, self._org_id).list_all()
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "description": s.description,
                "transport": s.transport,
                "command": s.command,
                "url": s.url,
                "config": json_safe(s.config or {}),
                "enabled": s.enabled,
                "has_secret": bool(s.secret_encrypted),  # value never exported
            }
            for s in servers
        ]

    async def _export_agents(self) -> list[dict[str, Any]]:
        agents = await AgentRepository(self._session, self._org_id).list_all()
        # supervisor_id / mcp_server_ids / workflow_allowlist are ORIGINAL ids,
        # remapped on import. grants holds tool NAMES (not ids) → no remap.
        return [
            {
                "id": str(a.id),
                "name": a.name,
                "display_name": a.display_name,
                "description": a.description,
                "kind": a.kind,
                "persona": a.persona,
                "provider": a.provider,
                "model": a.model,
                "params": json_safe(a.params or {}),
                "supervisor_id": str(a.supervisor_id) if a.supervisor_id else None,
                "avatar": a.avatar,
                "accent": a.accent,
                "enabled": a.enabled,
                "grants": json_safe(a.grants or {}),
                "mcp_server_ids": [str(x) for x in (a.mcp_server_ids or [])],
                "workflow_allowlist": [str(x) for x in (a.workflow_allowlist or [])],
            }
            for a in agents
        ]

    # ------------------------------------------------------------------ #
    # Data layer
    # ------------------------------------------------------------------ #
    async def _export_records(
        self, entities: list[dict[str, Any]], warnings: list[str], only_slugs: set[str] | None = None
    ) -> list[dict[str, Any]]:
        defs_repo = EntityDefinitionRepository(self._session, self._org_id)
        fields_repo = EntityFieldRepository(self._session, self._org_id)
        rels_repo = EntityRelationshipRepository(self._session, self._org_id)
        out: list[dict[str, Any]] = []
        for ent in entities:
            if only_slugs is not None and ent["slug"] not in only_slugs:
                continue
            definition = await defs_repo.get(uuid.UUID(ent["id"]))
            if definition is None:
                continue
            fields = await fields_repo.list_for_definition(definition.id)
            rels = await rels_repo.list_for_source(definition.id)
            repo = DynamicEntityRepository(self._session, self._org_id, definition, fields, rels)
            rows: list[dict[str, Any]] = []
            cursor = None
            while len(rows) < MAX_RECORDS_PER_ENTITY:
                page, cursor = await repo.list(cursor=cursor, limit=_PAGE)
                rows.extend(json_safe(_strip_record(r)) for r in page)
                if cursor is None:
                    break
            if cursor is not None:
                warnings.append(
                    f"entity {ent['slug']!r} has more than {MAX_RECORDS_PER_ENTITY} records; export truncated"
                )
            out.append({"entity_slug": ent["slug"], "records": rows})
        return out

    async def _export_documents(
        self, warnings: list[str], only_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        repo = DocumentRepository(self._session, self._org_id)
        out: list[dict[str, Any]] = []
        offset = 0
        while len(out) < MAX_DOCUMENTS:
            docs, total = await repo.list_for_folders(None, include_unfiled=True, offset=offset, limit=_PAGE)
            if not docs:
                break
            for d in docs:
                if only_ids is not None and str(d.id) not in only_ids:
                    continue
                out.append(
                    {
                        "id": str(d.id),
                        "title": d.title,
                        "description": d.description,
                        "text": d.text,
                        "folder_id": str(d.folder_id) if d.folder_id else None,
                        "tag_names": [t.name for t in d.tags],
                        "metadata": json_safe(d.metadata_ or {}),
                        "use_knowledge_graph": d.use_knowledge_graph,
                    }
                )
            offset += len(docs)
            if offset >= total:
                break
        if len(out) >= MAX_DOCUMENTS:
            warnings.append(f"more than {MAX_DOCUMENTS} documents; export truncated")
        return out


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    """Drop server-managed audit columns; keep ``id`` (for FK remapping) + field
    and to-one relationship slug values."""
    return {k: v for k, v in row.items() if k not in ("created_at", "updated_at")}
