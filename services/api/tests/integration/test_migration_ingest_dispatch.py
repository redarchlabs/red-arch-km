"""Integration tests for queuing ingestion after a bundle import (real PostgreSQL).

An imported document that is never queued for ingestion still *exists* — it just
has no vectors, so it is invisible to ``search_knowledge`` and to chat. Nothing in
the import response says so beyond a warning that blames the broker, which makes
this the kind of failure an operator discovers weeks later as "the knowledge base
is empty". These tests assert the queueing itself, with the dispatcher stubbed so
they do not need a live Celery broker.
"""

from __future__ import annotations

import uuid

import pytest
from api.config import get_settings
from api.models.org import Org
from api.services.migration import CollisionStrategy, MigrationImporter
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


def _bundle(documents: list[dict], tags: list[str] | None = None) -> dict:
    return {
        "kind": "km2-migration-bundle",
        "format_version": 2,
        "resources": {
            "tags": [{"id": str(uuid.uuid4()), "name": t} for t in (tags or [])],
            "documents": documents,
        },
    }


def _doc(title: str, *, text: str | None = "body text", tag_names: list[str] | None = None) -> dict:
    did = str(uuid.uuid4())
    return {
        "id": did,
        "lineage_id": did,
        "title": title,
        "description": None,
        "text": text,
        "folder_id": None,
        "tag_names": tag_names or [],
        "metadata": {},
        "use_knowledge_graph": True,
    }


async def _org(admin_session: AsyncSession, prefix: str) -> uuid.UUID:
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    return org.id


async def _import(admin_session: AsyncSession, org_id: uuid.UUID, bundle: dict, monkeypatch) -> list[dict]:
    """Run an import with the Celery dispatch captured instead of sent."""
    sent: list[dict] = []

    def _fake_dispatch(payload: dict) -> str:
        sent.append(payload)
        return f"task-{len(sent)}"

    monkeypatch.setattr("api.tasks.ingest.dispatch_ingest", _fake_dispatch)

    importer = MigrationImporter(admin_session, org_id, get_settings())
    summary = await importer.import_bundle(bundle, CollisionStrategy.SKIP, dry_run=False)
    # Production calls this after the import transaction commits.
    await admin_session.flush()
    await importer.dispatch_pending_ingests(summary)
    _import.summary = summary  # type: ignore[attr-defined]
    return sent


class TestIngestDispatch:
    async def test_an_imported_document_is_queued_for_ingestion(self, admin_session: AsyncSession, monkeypatch) -> None:
        """The regression: this raised MissingGreenlet on `doc.tags` and every
        imported document was left PENDING and unsearchable."""
        org_id = await _org(admin_session, "Ingest")

        sent = await _import(admin_session, org_id, _bundle([_doc("Charter")]), monkeypatch)

        assert len(sent) == 1
        assert sent[0]["title"] == "Charter"
        assert sent[0]["tenant_id"] == str(org_id)
        assert _import.summary.warnings == []  # type: ignore[attr-defined]

    async def test_tag_names_reach_the_ingest_payload(self, admin_session: AsyncSession, monkeypatch) -> None:
        """Tags scope retrieval, so losing them quietly narrows what a search finds."""
        org_id = await _org(admin_session, "Ingest")
        bundle = _bundle([_doc("Policy", tag_names=["policy", "hr"])], tags=["policy", "hr"])

        sent = await _import(admin_session, org_id, bundle, monkeypatch)

        assert sorted(sent[0]["tags"]) == ["hr", "policy"]

    async def test_every_document_in_a_multi_document_bundle_is_queued(
        self, admin_session: AsyncSession, monkeypatch
    ) -> None:
        org_id = await _org(admin_session, "Ingest")
        titles = [f"Doc {i}" for i in range(5)]

        sent = await _import(admin_session, org_id, _bundle([_doc(t) for t in titles]), monkeypatch)

        assert sorted(p["title"] for p in sent) == sorted(titles)

    async def test_a_document_with_no_text_is_not_queued(self, admin_session: AsyncSession, monkeypatch) -> None:
        """Nothing to embed: queueing it would just fail in the worker."""
        org_id = await _org(admin_session, "Ingest")

        sent = await _import(admin_session, org_id, _bundle([_doc("Empty", text=None)]), monkeypatch)

        assert sent == []

    async def test_a_dry_run_queues_nothing(self, admin_session: AsyncSession, monkeypatch) -> None:
        org_id = await _org(admin_session, "Ingest")
        captured: list[dict] = []
        monkeypatch.setattr("api.tasks.ingest.dispatch_ingest", lambda payload: captured.append(payload) or "t")

        importer = MigrationImporter(admin_session, org_id, get_settings())
        summary = await importer.import_bundle(_bundle([_doc("Draft")]), CollisionStrategy.SKIP, dry_run=True)
        await importer.dispatch_pending_ingests(summary)

        assert captured == []

    async def test_a_broker_outage_warns_without_failing_the_import(
        self, admin_session: AsyncSession, monkeypatch
    ) -> None:
        """The behaviour the original warning was written for — a real broker
        failure still leaves the row in place and only warns."""
        org_id = await _org(admin_session, "Ingest")

        def _boom(payload: dict) -> str:
            raise RuntimeError("broker unreachable")

        monkeypatch.setattr("api.tasks.ingest.dispatch_ingest", _boom)

        importer = MigrationImporter(admin_session, org_id, get_settings())
        summary = await importer.import_bundle(_bundle([_doc("Charter")]), CollisionStrategy.SKIP, dry_run=False)
        await admin_session.flush()
        await importer.dispatch_pending_ingests(summary)

        assert any("could not be queued" in w for w in summary.warnings)
        assert summary.outcome("documents").created == 1
