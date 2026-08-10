"""Knowledge tools — RAG lookups scoped to the person the agent is acting for.

Always-allowed read tools (every agent gets them, still kind-gated). They reuse
the same brain-api client the workflow ``knowledge_search`` action uses.

**An agent reads with its actor's eyes, not the org's.** This used to pass only a
``tenant_id``, so every agent retrieved from the whole organisation's knowledge
base while every member-facing search passed the caller's permission mask. An
agent could therefore quote a document to someone who is not cleared to read it —
and because an agent's answer is prose, the disclosure carried no citation trail
that would make it obvious. The masks now come from ``ctx.actor_user_id``, so an
agent is exactly as capable as the person who set it running.

A run with **no** actor (a schedule, an inbound webhook) has no one to inherit
from. That case fails closed: the tool refuses unless the agent's grants opt in
with ``knowledge_scope: "org"``, which is a deliberate, auditable admin decision
rather than a silent default.

**Another org, only when a person names one.** The same "actor's eyes" principle
says an agent should reach whatever its requester can reach, and a requester who
belongs to two orgs can read both. So ``org`` widens the search to a named
organisation — but only on an explicit instruction, never as a guess. The default
is and stays the agent's own org, because pulling org B's material into an org A
work order writes org B's content into org A's diary and artifacts, where it then
lives under org A's RLS. That crossing should happen because someone asked for
it, not because a retrieval came back thin.

The reach is the *actor's*, resolved fresh per call: their memberships, plus
every org for a site admin (whose elevation already grants org-wide reach
everywhere — see ``resolve_profile_access_keys``). The agent's own grants never
widen it, so two people running the same agent get two different reaches.

An unattended run cannot cross orgs at all. ``knowledge_scope: "org"`` is a grant
about *one* org — this one — and letting it carry across tenants would silently
turn every schedule and inbound webhook into a system-wide reader.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

if TYPE_CHECKING:
    from api.models.org import Org

logger = logging.getLogger(__name__)

# grants.knowledge_scope
SCOPE_ACTOR = "actor"  # default: see what the run's actor can see
SCOPE_ORG = "org"  # opt-in: org-wide, for unattended runs with no actor

# Words people tack onto an org's name when they say it out loud ("in the come
# follow me org"). Matching is already two-way substring, which covers these, but
# stripping them first keeps an exact-name hit exact rather than demoting it to a
# fuzzy one that could tie with a second org.
_ORG_SUFFIXES = (" organization", " organisation", " org")


def _scope(ctx: ToolContext) -> str:
    return str((ctx.agent.grants or {}).get("knowledge_scope") or SCOPE_ACTOR)


_SEARCH_PARAMS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Natural-language question to answer from the KB."},
        "org": {
            "type": "string",
            "description": (
                "Optional. The name of a DIFFERENT organization to search instead of your own. "
                "Leave it out unless someone has asked you, in words, to look in a named "
                "organization — your own org is the default and is nearly always the right "
                "answer. You can only reach orgs your requester can reach."
            ),
        },
    },
    "required": ["query"],
}


def _normalize(name: str) -> str:
    """Casefold and drop a trailing 'org'/'organization' so spoken names match."""
    wanted = name.strip().casefold()
    for suffix in _ORG_SUFFIXES:
        if wanted.endswith(suffix):
            return wanted[: -len(suffix)].strip()
    return wanted


def _match_orgs(orgs: list[Org], requested: str) -> list[Org]:
    """Candidate orgs for a requested name or id — 0 none, 1 hit, >1 ambiguous.

    Exact name beats substring so that an org whose name is contained in another
    ("Robots" inside "Robots (OpenAI)") is still reachable by naming it exactly.
    """
    try:
        wanted_id = uuid.UUID(requested.strip())
    except ValueError:
        pass
    else:
        return [o for o in orgs if o.id == wanted_id]

    wanted = _normalize(requested)
    if not wanted:
        return []
    exact = [o for o in orgs if _normalize(o.name) == wanted]
    if exact:
        return exact
    # Two-way: "come follow me lessons" should still find "Come Follow Me".
    return [o for o in orgs if wanted in _normalize(o.name) or _normalize(o.name) in wanted]


async def _reachable_orgs(session: Any, actor_user_id: uuid.UUID) -> list[Org]:
    """Every org this actor may read: their memberships, or all of them for a site admin."""
    from sqlalchemy import select

    from api.models.org import Org
    from api.models.user import UserOrgMembership, UserProfile

    profile = await session.get(UserProfile, actor_user_id)
    if profile is None:
        return []
    if profile.is_site_admin:
        return list((await session.execute(select(Org).order_by(Org.name))).scalars())
    rows = await session.execute(
        select(Org)
        .join(UserOrgMembership, UserOrgMembership.org_id == Org.id)
        .where(UserOrgMembership.profile_id == actor_user_id)
        .order_by(Org.name)
    )
    return list(rows.scalars())


async def _resolve_named_org(ctx: ToolContext, requested: str) -> dict[str, Any]:
    """Resolve a requested org name into a tenant + the actor's masks inside it.

    Runs on its **own** session with the cross-org bypass. The run's transaction is
    pinned to the agent's org, and ``user_org_memberships`` is one of the tables
    RLS pins — so resolving another org's membership on the run's session would
    find nothing and report "you have no access" for an org the actor is in fact a
    member of. A separate session keeps the run's own scope untouched: widening it
    in place would leave the bypass GUC on for every later tool call in the turn.

    Returns ``{"error": ...}`` or ``{"org_id", "name", "access_keys", "model"}``.
    """
    from api import db_scope
    from api.db import get_session_factory
    from api.services.org_llm import org_default_llm_model
    from api.services.search_access import resolve_profile_access_keys

    assert ctx.actor_user_id is not None  # callers check; keeps the type narrow

    async with get_session_factory(ctx.settings)() as privileged:
        await db_scope.enter_bypass(privileged)
        reachable = await _reachable_orgs(privileged, ctx.actor_user_id)
        matches = _match_orgs(reachable, requested)
        names = ", ".join(o.name for o in reachable)

        if not matches:
            return {
                "error": (
                    f"No organization matching {requested!r} that the person you are acting for "
                    f"can read. They have access to: {names or 'no other organization'}. Use one "
                    "of those names exactly, or search your own org instead."
                )
            }
        if len(matches) > 1:
            tied = ", ".join(o.name for o in matches)
            return {"error": f"{requested!r} matches more than one organization ({tied}). Name one exactly."}

        org = matches[0]
        access_keys = await resolve_profile_access_keys(privileged, org.id, ctx.actor_user_id)
        if access_keys == []:
            return {"error": f"You have no knowledge-base access in {org.name}."}
        return {
            "org_id": org.id,
            "name": org.name,
            "access_keys": access_keys,
            # The *target* org's pinned model, not the agent's: an org pinned to
            # local inference must not have its documents synthesised by a
            # third-party model just because the reader came from elsewhere.
            "model": await org_default_llm_model(privileged, org.id),
        }


async def _own_org_name(ctx: ToolContext) -> str | None:
    """The agent's own org name, for the result line. ``None`` when unavailable."""
    if ctx.session is None:
        return None
    from api.models.org import Org

    org = await ctx.session.get(Org, ctx.org_id)
    return org.name if org is not None else None


async def _search_knowledge(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    if ctx.settings is None:
        return {"error": "knowledge search is not configured"}
    from api.services.brain_client import BrainAPIClient
    from api.services.org_llm import org_default_llm_model
    from api.services.search_access import resolve_profile_access_keys

    requested = str(args.get("org") or "").strip()
    scope = _scope(ctx)

    if requested and ctx.actor_user_id is None:
        # No one to inherit reach from. knowledge_scope: "org" is a grant about
        # this org; it must not become a passport to every other one.
        return {
            "error": (
                "This run has no user to read on behalf of, so it cannot search another "
                "organization. Search your own org instead, or have a person start the run."
            )
        }

    if requested:
        target = await _resolve_named_org(ctx, requested)
        if "error" in target:
            return target
        target_org_id: uuid.UUID = target["org_id"]
        searched_org: str | None = target["name"]
        access_keys = target["access_keys"]
        model = target["model"]
    else:
        target_org_id = ctx.org_id
        searched_org = await _own_org_name(ctx)
        if ctx.actor_user_id is not None:
            access_keys = await resolve_profile_access_keys(ctx.session, ctx.org_id, ctx.actor_user_id)
            if access_keys == []:
                # A profile with no membership in this org. Distinct from None
                # (unrestricted) and from a restricted mask list — there is nothing
                # this actor may read, so say so rather than silently searching wide.
                return {"error": "You have no knowledge-base access in this organization."}
        elif scope == SCOPE_ORG:
            access_keys = None  # unattended, explicitly granted org-wide reach
        else:
            return {
                "error": (
                    "This run has no user to read on behalf of, so the knowledge base is "
                    "unavailable. An admin can grant this agent org-wide knowledge access "
                    'by setting grants.knowledge_scope to "org".'
                )
            }
        # Org-pinned answer model (local vs 3rd-party); None = brain-api default.
        model = await org_default_llm_model(ctx.session, ctx.org_id)

    client = BrainAPIClient(ctx.settings)
    try:
        result = await client.vector_chat(
            tenant_id=str(target_org_id),
            query=query,
            access_keys=access_keys,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 - surface as a tool error, don't crash the run
        # What the model does with this matters more than what it says. The raw
        # exception was an httpx repr — a 500, an internal URL and a link to the
        # httpx docs — and an agent reading it concluded it had no way to search
        # local knowledge at all. It then asked the person who filed the order for
        # "the exact knowledge-graph or internal host so I can query it directly",
        # which is not a thing anyone can supply, and parked the order on it. The
        # search layer was simply down (an embedding-width mismatch after a
        # config change); the tool was right, the service was broken.
        logger.warning("knowledge search failed for org %s", target_org_id, exc_info=True)
        return {
            "error": (
                "The knowledge service failed to answer — this is a fault in the platform, not a "
                "missing capability and not something you lack permission for. You DO have "
                "knowledge search; it is temporarily unavailable. Try once more, and if it fails "
                "again say so plainly and carry on with what you can do without it. Do not ask a "
                "person for a host, a URL, an endpoint or database access: there is nothing they "
                "can give you that would change this. "
                f"({type(exc).__name__})"
            )
        }
    answer = result.get("answer") or result.get("response") or result.get("result")
    sources = result.get("sources") or result.get("citations") or []
    # Trim source payloads so the tool result stays compact for the model.
    trimmed = [
        {k: s.get(k) for k in ("document_key", "title", "snippet", "section") if isinstance(s, dict) and k in s}
        for s in sources[:5]
        if isinstance(s, dict)
    ]
    # Name the org that was actually searched. An agent told "check the come follow
    # me org", searching its own and finding nothing, reported that the material was
    # missing *from the org it never touched* — an empty result from one tenant
    # presented as proof of absence in another. The model cannot report the wrong
    # org if the result tells it which one answered.
    return {
        "answer": answer,
        "sources": trimmed,
        "searched_org": searched_org or str(target_org_id),
    }


SEARCH_KNOWLEDGE = ToolSpec(
    name="search_knowledge",
    description=(
        "Answer a question from an organization's knowledge base (RAG) — the documents, notes "
        "and files uploaded to it. The right first move for anything that might already be "
        "written down. By default it searches YOUR OWN org, which is nearly always what you "
        "want. Pass `org` with an organization's name ONLY when someone has asked you, in "
        "words, to look somewhere else; you can reach the orgs the person you are acting for "
        "can reach, not the whole system. Every result names the org it actually searched: "
        "report that org, and never claim you searched one you did not."
    ),
    parameters=_SEARCH_PARAMS,
    category=Category.READ,
    handler=_search_knowledge,
    always_allowed=True,
)
