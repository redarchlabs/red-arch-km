"""Shared single-node action execution for the token engine.

``ActionExecutor`` builds an :class:`ActionContext` and runs one task node's
handler from the existing ``ACTION_REGISTRY`` — the same handlers, SSRF/email/
form wiring, and ``$ref``/template resolution the legacy dispatcher uses. Keeping
it here (rather than reaching into ``WorkflowDispatchService`` internals) gives
the token engine a clean, testable primitive and is the seam the Phase-4
``tasks/`` package grows from. The legacy dispatcher keeps its own copy for now;
the two converge when the walker is retired.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.custom_entity import EntityDefinition
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import OutboxWriter
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError


@dataclass
class ActionResult:
    """Outcome of executing one task node."""

    ok: bool
    output: dict[str, Any] | None = None
    error: str | None = None


class ActionExecutor:
    """Executes a single action/task node against a run's tenant session."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        webhook_allowlist: tuple[str, ...] = (),
        trusted_local_hosts: tuple[str, ...] = (),
        public_base_url: str = "",
        email_sender: Any = None,
        org_encryption_key: str = "",
    ) -> None:
        self._session = session
        self._webhook_allowlist = webhook_allowlist
        self._trusted_local_hosts = trusted_local_hosts
        self._public_base_url = public_base_url
        self._email_sender = email_sender
        # Key for decrypting connection secrets at execute time. Empty = connector
        # tasks can't resolve secrets (dry-run / unconfigured paths).
        self._org_encryption_key = org_encryption_key

    async def execute(
        self,
        *,
        org_id: uuid.UUID,
        action_type: str,
        config: dict[str, Any],
        record_id: uuid.UUID | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        entity_definition_id: uuid.UUID | None,
        origin_run_id: uuid.UUID,
        inputs: dict[str, Any] | None = None,
    ) -> ActionResult:
        """Run one action handler; never raises — failures come back on the result."""
        handler = ACTION_REGISTRY.get(action_type)
        if handler is None:
            return ActionResult(ok=False, error=f"unknown action type: {action_type!r}")

        repo_cache: dict[Any, DynamicEntityRepository] = {}

        async def _trigger_repo() -> DynamicEntityRepository:
            if entity_definition_id is None:
                raise ActionError("action requires a triggering entity")
            key = ("id", entity_definition_id)
            if key not in repo_cache:
                repo_cache[key] = await self._repo_by_id(org_id, entity_definition_id, origin_run_id)
            return repo_cache[key]

        async def _repo_for_slug(slug: str) -> DynamicEntityRepository:
            key = ("slug", slug)
            if key not in repo_cache:
                repo_cache[key] = await self._repo_by_slug(org_id, slug, origin_run_id)
            return repo_cache[key]

        ctx = ActionContext(
            org_id=org_id,
            record_id=record_id,
            before=before,
            after=after,
            inputs=inputs or {},
            config=config or {},
            trigger_repo=_trigger_repo,
            repo_for_slug=_repo_for_slug,
            webhook_allowlist=self._webhook_allowlist,
            trusted_local_hosts=self._trusted_local_hosts,
            mint_form_link=lambda form_id, rid, email: self._mint_form_link(org_id, form_id, rid, email),
            send_email=self._send_email,
            resolve_connection=lambda name: self._resolve_connection(org_id, name),
        )
        try:
            output = await handler.execute(ctx)
            return ActionResult(ok=True, output=output)
        except Exception as exc:  # noqa: BLE001 - recorded on the step, not raised
            return ActionResult(ok=False, error=str(exc))

    # ---- collaborators (mirror WorkflowDispatchService's wiring) --------- #
    async def _resolve_connection(self, org_id: uuid.UUID, name: str) -> Any:
        """Load a named connection (org-scoped) and decrypt its secret. Returns a
        ResolvedConnection or None. The plaintext secret exists only here + in the
        handler call — never persisted."""
        from api.repositories.workflow import WorkflowConnectionRepository
        from api.services.crypto import decrypt_secret
        from api.services.workflow.actions import ResolvedConnection

        if not self._org_encryption_key:
            return None
        conn = await WorkflowConnectionRepository(self._session, org_id).get_by_name(name)
        if conn is None:
            return None
        secret = (
            decrypt_secret(conn.secret_encrypted, self._org_encryption_key)
            if conn.secret_encrypted
            else None
        )
        return ResolvedConnection(
            name=conn.name,
            base_url=conn.base_url,
            auth_type=conn.auth_type,
            secret=secret,
            config=conn.config or {},
        )

    async def _send_email(self, to: str, subject: str, body: str) -> bool:
        if self._email_sender is None or not self._email_sender.is_configured():
            return False
        await self._email_sender.send(to=to, subject=subject, text=body)
        return True

    async def _mint_form_link(
        self, org_id: uuid.UUID, form_id: uuid.UUID, record_id: uuid.UUID, recipient: str | None
    ) -> tuple[str, bool]:
        from api.schemas.form import GenerateLinkRequest
        from api.services.form_service import FormService

        service = FormService(
            self._session,
            org_id,
            public_base_url=self._public_base_url,
            email_sender=self._email_sender,
        )
        _link, _token, url, email_sent = await service.generate_link(
            form_id, GenerateLinkRequest(target_record_id=record_id, recipient_email=recipient)
        )
        return url, email_sent

    async def _repo_by_id(
        self, org_id: uuid.UUID, definition_id: uuid.UUID, origin_run_id: uuid.UUID
    ) -> DynamicEntityRepository:
        definition = await EntityDefinitionRepository(self._session, org_id).get(definition_id)
        if definition is None:
            raise ActionError("entity definition not found")
        return await self._build_repo(org_id, definition, origin_run_id)

    async def _repo_by_slug(
        self, org_id: uuid.UUID, slug: str, origin_run_id: uuid.UUID
    ) -> DynamicEntityRepository:
        definition = await EntityDefinitionRepository(self._session, org_id).get_by_slug(slug)
        if definition is None:
            raise ActionError(f"entity not found: {slug!r}")
        return await self._build_repo(org_id, definition, origin_run_id)

    async def _build_repo(
        self, org_id: uuid.UUID, definition: EntityDefinition, origin_run_id: uuid.UUID
    ) -> DynamicEntityRepository:
        fields = await EntityFieldRepository(self._session, org_id).list_for_definition(definition.id)
        rels = await EntityRelationshipRepository(self._session, org_id).list_for_source(definition.id)
        return DynamicEntityRepository(
            self._session,
            org_id,
            definition,
            fields,
            rels,
            outbox=OutboxWriter(self._session),
            origin_run_id=origin_run_id,
        )
