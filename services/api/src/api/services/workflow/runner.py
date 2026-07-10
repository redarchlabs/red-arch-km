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


def _format_passages(hits: list[dict[str, Any]]) -> str:
    """Render retrieved chunks into a numbered context block (mirrors brain-api's
    RAG context format) so a downstream LLM can ground on the raw passages."""
    parts: list[str] = []
    for number, hit in enumerate(hits, 1):
        payload = hit.get("payload", {}) if isinstance(hit, dict) else {}
        title = payload.get("document_title") or "Untitled"
        section = payload.get("section")
        label = f"{title} — {section}" if section else title
        parts.append(f"[{number}] {label}\n{payload.get('text', '')}")
    return "\n\n".join(parts)


def _passage_sources(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lightweight, 1:1 numbered source list for retrieved passages (parity with
    brain-api's ``sources`` so downstream steps/logging see the same shape)."""
    sources: list[dict[str, Any]] = []
    for number, hit in enumerate(hits, 1):
        payload = hit.get("payload", {}) if isinstance(hit, dict) else {}
        sources.append(
            {
                "document_title": payload.get("document_title", ""),
                "document_key": payload.get("document_key", ""),
                "section": payload.get("section"),
                "score": hit.get("score") if isinstance(hit, dict) else None,
                "number": number,
            }
        )
    return sources


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
        settings: Any = None,
    ) -> None:
        self._session = session
        self._webhook_allowlist = webhook_allowlist
        self._trusted_local_hosts = trusted_local_hosts
        self._public_base_url = public_base_url
        self._email_sender = email_sender
        # Key for decrypting connection secrets at execute time. Empty = connector
        # tasks can't resolve secrets (dry-run / unconfigured paths).
        self._org_encryption_key = org_encryption_key
        # App Settings — needed to reach brain-api for the knowledge_search
        # action. None = knowledge search unavailable (the action raises a clear
        # error rather than silently no-op'ing).
        self._settings = settings

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
        variables: dict[str, Any] | None = None,
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
            vars=variables or {},
            config=config or {},
            trigger_repo=_trigger_repo,
            repo_for_slug=_repo_for_slug,
            webhook_allowlist=self._webhook_allowlist,
            trusted_local_hosts=self._trusted_local_hosts,
            mint_form_link=lambda form_id, rid, email: self._mint_form_link(org_id, form_id, rid, email),
            send_email=self._send_email,
            resolve_connection=lambda name: self._resolve_connection(org_id, name),
            search_knowledge=lambda opts: self._search_knowledge(org_id, opts),
            retrieve_knowledge=lambda query: self._retrieve_knowledge(org_id, query),
            summarize=lambda opts: self._summarize(org_id, opts),
            decide=lambda opts: self._decide(org_id, opts),
        )
        try:
            output = await handler.execute(ctx)
            return ActionResult(ok=True, output=output)
        except Exception as exc:  # noqa: BLE001 - recorded on the step, not raised
            return ActionResult(ok=False, error=str(exc))

    # ---- collaborators (mirror WorkflowDispatchService's wiring) --------- #
    async def _search_knowledge(self, org_id: uuid.UUID, opts: dict[str, Any]) -> dict[str, Any]:
        """Org-scoped hybrid RAG lookup for the knowledge_search action.

        Runs unrestricted within the org (``access_keys=None``): a workflow is a
        trusted org-level automation, not an end user, so it sees the org's whole
        knowledge base. ``opts`` carries the ``query`` and a ``use_knowledge_graph``
        toggle (default true) so a per-run switch can skip the sequential graph hop.
        Deferred import keeps brain-api off the hot import path."""
        if self._settings is None:
            raise ActionError("knowledge search requires Settings (not wired in this context)")
        from api.services.brain_client import BrainAPIClient

        client = BrainAPIClient(self._settings)
        return await client.vector_chat(
            tenant_id=str(org_id),
            query=str(opts.get("query", "")),
            access_keys=None,
            use_knowledge_graph=bool(opts.get("use_knowledge_graph", True)),
        )

    async def _retrieve_knowledge(self, org_id: uuid.UUID, query: str) -> dict[str, Any]:
        """Retrieval-ONLY KB lookup for knowledge_search(synthesize=False): vector
        search → formatted passage context, with NO brain-api LLM synthesis. Pairs
        with a downstream llm_decide that does the single grounded generation, so a
        robot turn costs one LLM call instead of two (brain-api RAG + llm_decide)."""
        if self._settings is None:
            raise ActionError("knowledge retrieval requires Settings (not wired in this context)")
        from api.services.brain_client import BrainAPIClient

        client = BrainAPIClient(self._settings)
        result = await client.vector_search(tenant_id=str(org_id), query=query, access_keys=None)
        hits = result.get("hits", [])
        return {"answer": _format_passages(hits), "sources": _passage_sources(hits), "passages": hits}

    async def _summarize(self, org_id: uuid.UUID, opts: dict[str, Any]) -> str:
        """Small-LLM condensation for the summarize action. Uses the org's OpenAI
        key (falls back to the central key), mirroring the RAG/agent key precedence."""
        if self._settings is None:
            raise ActionError("summarization requires Settings (not wired in this context)")
        key = await self._org_openai_key(org_id) or self._settings.openai_api_key.get_secret_value()
        if not key:
            raise ActionError("summarization requires an OpenAI API key (org or central)")
        from openai import AsyncOpenAI

        from api.services.spoken_summary import summarize_for_speech

        client = AsyncOpenAI(api_key=key)
        model = opts.get("model") or self._settings.openai_summary_model
        return await summarize_for_speech(
            client,
            model,
            text=str(opts.get("text") or ""),
            question=opts.get("question"),
            max_words=int(opts.get("max_words") or 30),
            instruction=opts.get("instruction"),
        )

    async def _decide(self, org_id: uuid.UUID, opts: dict[str, Any]) -> dict[str, Any]:
        """Constrained-LLM steering for the llm_decide action. Uses the org's OpenAI key
        (falls back to the central key) and the chat model, mirroring _summarize's wiring."""
        if self._settings is None:
            raise ActionError("llm_decide requires Settings (not wired in this context)")
        key = await self._org_openai_key(org_id) or self._settings.openai_api_key.get_secret_value()
        if not key:
            raise ActionError("llm_decide requires an OpenAI API key (org or central)")
        from openai import AsyncOpenAI

        from api.services.llm_decide import decide_action

        client = AsyncOpenAI(api_key=key)
        model = opts.get("model") or self._settings.openai_model
        return await decide_action(
            client,
            model,
            question=str(opts.get("question") or ""),
            context=str(opts.get("context") or ""),
            gestures=list(opts.get("gestures") or []),
            moods=list(opts.get("moods") or []),
            system=opts.get("system"),
            history=opts.get("history"),
        )

    async def _org_openai_key(self, org_id: uuid.UUID) -> str | None:
        """The org's own OpenAI key (decrypted), or None to fall back to central."""
        from api.models.org import Org
        from api.services.crypto import decrypt_secret

        org = await self._session.get(Org, org_id)
        stored = org.openai_api_key if org else None
        if not stored:
            return None
        return decrypt_secret(stored, self._settings.org_encryption_key.get_secret_value())

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
        secret = decrypt_secret(conn.secret_encrypted, self._org_encryption_key) if conn.secret_encrypted else None
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

    async def _repo_by_slug(self, org_id: uuid.UUID, slug: str, origin_run_id: uuid.UUID) -> DynamicEntityRepository:
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
