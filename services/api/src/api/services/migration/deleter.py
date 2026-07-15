"""Deletion propagation for promotions (opt-in, config-layer only).

The importer is additive: it never removes an object that the source dropped. A
promotion (and a rollback) sometimes needs to DELETE — remove a target object that
is no longer in the release, or remove an object a promotion created. That lives
here, deliberately separate and gated, because deletion is the sharp edge.

Objects are addressed by durable ``lineage_id`` (never a mutable natural key — a
rename would otherwise look like delete-old + add-new). Deletes run in REVERSE
dependency order (children before parents). Data (records) and folders have no
safe generic delete path here and are reported as skipped rather than guessed at.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.repositories.agent import AgentRepository
from api.repositories.custom_entity import EntityDefinitionRepository
from api.repositories.document import DocumentRepository
from api.repositories.form import FormRepository
from api.repositories.mcp_server import McpServerRepository
from api.repositories.report import ReportRepository
from api.repositories.tag import TagRepository
from api.repositories.view import ViewRepository
from api.repositories.workflow import (
    WorkflowConnectionRepository,
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
)
from api.services.entity_service import EntityError, EntityService
from api.services.migration.bundle import RESOURCE_ORDER, ImportSummary

# Resource types this module will NOT delete: records/documents are DATA (records
# have no lineage; deleting them is destructive to user data) and folders have no
# safe generic delete path. They are reported as skipped.
DATA_RESOURCE_TYPES = frozenset({"records"})
_UNSUPPORTED = frozenset({"folders", "records"})

# Deleting these also destroys user DATA even though they are "config": dropping an
# entity tears down its physical ``ce_`` table and every record row in it. So they
# require the stronger ``allow_data`` gate, not just ``apply_deletes``.
_DATA_DESTRUCTIVE = frozenset({"entities"})


class MigrationDeleter:
    """Remove target objects addressed by ``lineage_id``, in reverse dependency order."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID, settings: Settings) -> None:
        self._session = session
        self._org_id = org_id
        self._settings = settings

    async def delete_lineages(
        self,
        deletions: dict[str, set[str]],
        summary: ImportSummary,
        *,
        allow_data: bool = False,
    ) -> dict[str, int]:
        """Delete the given ``{resource_type: {lineage_id, ...}}`` from the target.

        Returns a per-type count actually deleted. ``allow_data`` is required to
        touch data-layer types (records) — off by default. Unsupported types
        (folders, records) are reported in ``summary.warnings`` and left in place.
        """
        deleted: dict[str, int] = {}
        # Reverse dependency order: children (views/forms/agents) before parents
        # (entities), so a delete never orphans a still-referenced object.
        for rtype in reversed(RESOURCE_ORDER):
            wanted = deletions.get(rtype)
            if not wanted:
                continue
            if rtype in _UNSUPPORTED:
                summary.warnings.append(
                    f"{len(wanted)} {rtype} marked for deletion were left in place "
                    f"({rtype} deletion is not applied automatically)"
                )
                continue
            if rtype in (DATA_RESOURCE_TYPES | _DATA_DESTRUCTIVE) and not allow_data:
                summary.warnings.append(
                    f"{len(wanted)} {rtype} marked for deletion were left in place "
                    f"(deleting {rtype} destroys user data; requires allow_data)"
                )
                continue
            count = await self._delete_type(rtype, wanted, summary)
            if count:
                deleted[rtype] = count
        return deleted

    async def _delete_type(self, rtype: str, lineages: set[str], summary: ImportSummary) -> int:
        rows_by_lineage = await self._current_by_lineage(rtype)
        count = 0
        for lineage in lineages:
            row = rows_by_lineage.get(lineage)
            if row is None:
                continue  # already gone
            try:
                await self._delete_row(rtype, row)
                count += 1
            except (EntityError, ValueError) as exc:
                summary.warnings.append(f"could not delete {rtype} {self._label(row)!r}: {exc}")
        return count

    async def _current_by_lineage(self, rtype: str) -> dict[str, Any]:
        rows = await self._load_current(rtype)
        return {str(getattr(r, "lineage_id", None) or r.id): r for r in rows}

    async def _load_current(self, rtype: str) -> list[Any]:
        if rtype == "tags":
            rows, _ = await TagRepository(self._session, self._org_id).list_all(limit=10_000)
            return list(rows)
        if rtype == "entities":
            rows, _ = await EntityDefinitionRepository(self._session, self._org_id).list_all(limit=1000)
            return list(rows)
        if rtype == "connections":
            return list(await WorkflowConnectionRepository(self._session, self._org_id).list_all())
        if rtype == "workflows":
            return list(await WorkflowRepository(self._session, self._org_id).list_all())
        if rtype == "inbound_endpoints":
            return list(await WorkflowInboundEndpointRepository(self._session, self._org_id).list_all())
        if rtype == "reports":
            return list(await ReportRepository(self._session, self._org_id).list_all())
        if rtype == "forms":
            return list(await FormRepository(self._session, self._org_id).list_all())
        if rtype == "views":
            return list(await ViewRepository(self._session, self._org_id).list_all())
        if rtype == "mcp_servers":
            return list(await McpServerRepository(self._session, self._org_id).list_all())
        if rtype == "agents":
            return list(await AgentRepository(self._session, self._org_id).list_all())
        if rtype == "documents":
            return await self._all_documents()
        return []

    async def _all_documents(self) -> list[Any]:
        repo = DocumentRepository(self._session, self._org_id)
        out: list[Any] = []
        off = 0
        while True:
            docs, total = await repo.list_for_folders(None, include_unfiled=True, offset=off, limit=200)
            out.extend(docs)
            off += len(docs)
            if not docs or off >= total:
                break
        return out

    async def _delete_row(self, rtype: str, row: Any) -> None:
        org = self._org_id
        s = self._session
        if rtype == "tags":
            await TagRepository(s, org).delete(row.id)
        elif rtype == "entities":
            # force=True: promotion deletes are intentional; tear down referencing
            # relationships + the physical ce_ table.
            await EntityService(s, org).drop_definition(row.id, force=True)
        elif rtype == "connections":
            await WorkflowConnectionRepository(s, org).delete(row)
        elif rtype == "workflows":
            await WorkflowRepository(s, org).delete(row)
        elif rtype == "inbound_endpoints":
            await WorkflowInboundEndpointRepository(s, org).delete(row)
        elif rtype == "reports":
            await ReportRepository(s, org).delete(row)
        elif rtype == "forms":
            await FormRepository(s, org).delete(row)
        elif rtype == "views":
            await ViewRepository(s, org).delete(row)
        elif rtype == "mcp_servers":
            await McpServerRepository(s, org).delete(row)
        elif rtype == "agents":
            await AgentRepository(s, org).delete(row)
        elif rtype == "documents":
            await DocumentRepository(s, org).delete(row.id)

    @staticmethod
    def _label(row: Any) -> str:
        return str(getattr(row, "name", None) or getattr(row, "slug", None) or getattr(row, "title", None) or row.id)
