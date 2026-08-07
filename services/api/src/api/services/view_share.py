"""Anonymous (unauthenticated) access to individual views.

Some screens are used by people who will never have a KM2 login — a crew station
on a shared tablet, a status board in a corridor, a check-in pad at a front desk.
This grants access to ONE view at a time, by an unguessable token in the URL, and
nothing else.

The security model, in full:

* **Off by default, opt-in per view.** There is no global "make views public"
  switch; an org admin enables one view at a time and can revoke it instantly.
* **The token is the credential.** 32 bytes of entropy, shown once, stored only as
  a SHA-256 hash — a database read cannot recover a working link. Anyone holding
  the link has the access, so it is shared like a door key, not a password.
* **The record is fixed by the server.** An anonymous render resolves either the
  record captured when sharing was enabled, or — in ``public_record_follow`` mode
  — the entity's newest record, recomputed per request. Which record is decided
  from the view's own row either way; nothing is read from the request, so a token
  cannot be pointed at another row. Follow mode exists because a page about
  "whatever is happening now" (a class quiz, where each lesson creates a new
  session) would otherwise go stale the moment the next one starts, and fail by
  rendering a dead row's empty fields — indistinguishable from a broken page.
* **Workflows are bounded by the page.** The only workflows an anonymous caller
  may start are the ones the view's own element tree references. That set is
  derived from the config at request time, so it cannot drift: remove a button
  from the view and the permission goes with it. A leaked token can therefore do
  what the page does — never "run anything in the org".
* **Rate limited per token**, so a leaked link cannot be hammered.
* **Runs are attributed to nobody.** ``actor_user_id`` is None, so an anonymous
  run is distinguishable from a member's in the run history.

What it deliberately does NOT grant: the entity-record REST API, document access,
search, or any other view. Elements that call those endpoints directly from the
browser (``record_list``, ``chat``) will not load on an anonymous page — see
``PUBLIC_UNSUPPORTED_ELEMENTS``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.config import Settings
from api.models.view import View
from api.repositories.org import OrgRepository
from api.repositories.view import ViewRepository
from api.schemas.form import FormRenderRead, OrgBrandingRead
from api.schemas.workflow import ManualRunRequest, ManualRunResult
from api.services import form_token
from api.services.form_layout import collect_workflow_ids
from api.services.form_service import FormNotFoundError, FormValidationError
from api.services.self_record import resolve_latest_record_id
from api.services.view_service import ViewService

# Elements that fetch from authenticated endpoints of their own accord. They render
# but stay empty on an anonymous page, so the authoring UI can warn instead of the
# operator discovering it in front of an audience.
# ``stat`` joins the list for the same reason as ``report``: it reads a saved
# report through the authenticated reports endpoint. (``card`` is pure layout and
# needs no entry — the walk below recurses through any container generically.)
PUBLIC_UNSUPPORTED_ELEMENTS = ("record_list", "chat", "report", "stat", "form_ref")


class ViewShareError(FormValidationError):
    """Sharing is off, expired, or the request is outside what the link allows."""


def share_is_live(view: View, now: datetime | None = None) -> bool:
    """True when the view currently accepts anonymous requests."""
    if not view.public_token_hash or not view.is_active:
        return False
    if view.public_expires_at is None:
        return True
    return view.public_expires_at > (now or datetime.now(UTC))


def unsupported_elements(config: dict[str, Any]) -> list[str]:
    """Element types present in this view that cannot work without a login.

    Returned to the authoring UI at enable time so the warning is visible while
    the decision is being made, rather than being discovered by a blank panel on a
    tablet. Reads the raw config dict, so it works before validation.
    """
    found: set[str] = set()

    def walk(items: Any) -> None:
        if isinstance(items, dict):
            etype = items.get("type")
            if isinstance(etype, str) and etype in PUBLIC_UNSUPPORTED_ELEMENTS:
                found.add(etype)
            for value in items.values():
                walk(value)
        elif isinstance(items, list):
            for item in items:
                walk(item)

    walk(config.get("elements", []))
    return sorted(found)


class ViewShareAdminService:
    """Enable / disable anonymous access. Runs on the caller's tenant session, so
    the usual org scoping and org-admin gate apply."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def _get(self, view_id: uuid.UUID) -> View:
        view = await ViewRepository(self._session, self._org_id).get(view_id)
        if view is None:
            raise FormNotFoundError("view not found")
        return view

    async def enable(
        self,
        view_id: uuid.UUID,
        *,
        record_id: uuid.UUID | None,
        expires_at: datetime | None,
        record_follow: bool = False,
        show_branding: bool = False,
    ) -> tuple[View, str]:
        """Turn sharing on (or rotate an existing link) and return the raw token.

        Rotating is the same call: a fresh token replaces the old one, which stops
        working immediately. That is the recovery path for a link that has been
        shared too widely.

        ``record_follow`` chooses which record the link is about for its whole life:
        a fixed ``record_id`` for a page about one thing, or the entity's newest
        record per request for a page about whatever is happening now. They are
        mutually exclusive — storing both would leave a stale id sitting behind a
        link that ignores it, which is exactly the confusion this option exists to
        remove.
        """
        view = await self._get(view_id)
        raw, token_hash = form_token.generate_token()
        view.public_token_hash = token_hash
        view.public_record_follow = record_follow
        view.public_record_id = None if record_follow else record_id
        view.public_expires_at = expires_at
        view.public_enabled_at = datetime.now(UTC)
        view.public_show_branding = show_branding
        await self._session.flush()
        return view, raw

    async def disable(self, view_id: uuid.UUID) -> View:
        view = await self._get(view_id)
        view.public_token_hash = None
        view.public_record_id = None
        view.public_record_follow = False
        view.public_expires_at = None
        view.public_enabled_at = None
        view.public_show_branding = False
        await self._session.flush()
        return view


class PublicViewService:
    """The unauthenticated path. Receives the PRIVILEGED session because the token
    must be resolved to an org before any tenant context exists; scopes down to
    that org before reading or writing anything."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _resolve(self, raw_token: str) -> View:
        # Hash lookup on the bypass session — this is the one query that must see
        # across orgs, and it reads nothing but the view row itself.
        result = await self._session.execute(
            select(View).where(View.public_token_hash == form_token.hash_token(raw_token))
        )
        view = result.scalar_one_or_none()
        # Same error for "no such token", "expired" and "switched off": a caller
        # holding a bad token learns only that it does not work.
        if view is None or not share_is_live(view):
            raise FormNotFoundError("this link is not available")
        await db_scope.enter_tenant(self._session, view.org_id)
        return view

    async def _record_id(self, view: View) -> uuid.UUID | None:
        """Which record this link is about, right now.

        Either the id captured at enable time, or — in follow mode — the entity's
        newest record resolved fresh on every request. Both are decided here on the
        server from the view's own row; neither reads anything from the caller, so
        the token still cannot be walked onto a record of the sharer's choosing.
        """
        if not view.public_record_follow:
            return view.public_record_id
        if view.entity_definition_id is None:
            # Follow means nothing without an entity to follow. A standalone view
            # renders unbound anyway, so this is not an error — just no record.
            return None
        return await resolve_latest_record_id(self._session, view.org_id, view.entity_definition_id)

    async def render(self, raw_token: str) -> FormRenderRead:
        view = await self._resolve(raw_token)
        # The resolved record only — `record_id` is never taken from the request, so
        # the link cannot be walked onto another row.
        render = await ViewService(self._session, view.org_id).render(view.id, await self._record_id(view))
        trimmed = _trim_catalog(render)
        # Org identity rides along ONLY when this link opted in. Off (the default)
        # the page stays anonymous chrome, which is what a link that can be
        # forwarded anywhere should be by default.
        if view.public_show_branding:
            org = await OrgRepository(self._session).get(view.org_id)
            if org is not None:
                trimmed.branding = OrgBrandingRead(
                    org_name=org.name,
                    accent_color=org.accent_color,
                    has_logo=bool(org.logo_object_key),
                )
        return trimmed

    async def logo(self, raw_token: str) -> tuple[str, uuid.UUID]:
        """The stored logo key for a branded share link.

        Behind the token (not the org id) so it inherits the link's own lifetime
        and rate limit, and so a link with branding switched off cannot be used to
        read the org's logo anyway.
        """
        view = await self._resolve(raw_token)
        if not view.public_show_branding:
            raise ViewShareError("this link does not show branding")
        org = await OrgRepository(self._session).get(view.org_id)
        if org is None or not org.logo_object_key:
            raise FormNotFoundError("no logo")
        return org.logo_object_key, view.org_id

    async def run_workflow(
        self,
        raw_token: str,
        workflow_id: uuid.UUID,
        *,
        inputs: dict[str, Any] | None,
        after: dict[str, Any] | None,
        settings: Settings,
    ) -> ManualRunResult:
        """Start one of the view's OWN workflows on behalf of an anonymous caller."""
        from api.repositories.workflow import WorkflowRepository
        from api.services.workflow.manual_run import execute_workflow_run, resolve_published_version

        view = await self._resolve(raw_token)

        # The allow-list, recomputed from the live config on every call.
        allowed = collect_workflow_ids(_parsed_elements(view))
        if workflow_id not in allowed:
            raise ViewShareError("this page cannot run that workflow")

        wf = await WorkflowRepository(self._session, view.org_id).get(workflow_id)
        if wf is None:
            raise FormNotFoundError("workflow not found")
        if not wf.enabled:
            # Distinct from "not found" on purpose. Folding the two together produced a
            # 404 on a button that plainly exists, which reads as a broken app rather
            # than a switch someone turned off — and sent the operator hunting for a
            # missing workflow instead of looking at the flag.
            raise ViewShareError("this workflow is disabled")
        version = await resolve_published_version(self._session, view.org_id, wf)

        request = ManualRunRequest(
            operation="update",
            # Resolved the same way the render was, so a button acts on the record the
            # page is showing. Pinned mode is unchanged; follow mode means the join
            # button on a class quiz targets THIS lesson rather than the one that
            # happened to be open when the link was made.
            record_id=await self._record_id(view),
            after=after or {},
            inputs=inputs or {},
        )
        return await execute_workflow_run(
            self._session,
            view.org_id,
            wf,
            version,
            request=request,
            # No actor: an anonymous run must not be attributed to a person, and the
            # run history should show it for what it is.
            actor_user_id=None,
            settings=settings,
        )


def _trim_catalog(render: FormRenderRead) -> FormRenderRead:
    """Cut the field catalogue down to what the page actually shows.

    The render payload carries entity metadata so the client knows how to draw
    each control. For a signed-in member that is unremarkable — they can see the
    schema anyway. For a stranger holding a link it is a disclosure: the full
    field list of every entity the view touches, names, types and picklist options
    included. On a quiz page that means the existence of ``correct_choice``; on a
    business one it means the shape of the record.

    So the anonymous copy keeps only the fields whose values were sent — the ones
    the renderer needs — and drops the rest. Values themselves are already limited
    to what the view declares, so this closes the metadata half of the same idea.
    """
    visible: set[str] = set(render.values or {})
    for container in (render.related or {}).values():
        payload = container if isinstance(container, dict) else getattr(container, "__dict__", {})
        values = payload.get("values") if isinstance(payload, dict) else None
        if isinstance(values, dict):
            visible.update(values)
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if isinstance(rows, list):
            for row in rows:
                row_values = row.get("values") if isinstance(row, dict) else None
                if isinstance(row_values, dict):
                    visible.update(row_values)

    trimmed = []
    for entity in render.catalog or []:
        data = entity.model_dump() if hasattr(entity, "model_dump") else dict(entity)
        data["fields"] = [f for f in data.get("fields", []) if f.get("slug") in visible]
        trimmed.append(type(entity).model_validate(data) if hasattr(entity, "model_dump") else data)
    return render.model_copy(update={"catalog": trimmed})


def _parsed_elements(view: View) -> list[Any]:
    """The view's config as validated element models, for the allow-list walk.

    Parsed rather than read as raw dicts so the walk sees the same shapes the
    renderer does; a config that no longer validates yields no workflows, which
    fails closed.
    """
    from api.schemas.form import FormConfig

    try:
        return FormConfig.model_validate(view.config or {"version": 2, "elements": []}).elements
    except Exception:  # noqa: BLE001 - a config we cannot parse grants nothing
        return []
