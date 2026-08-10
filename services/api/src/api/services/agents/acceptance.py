"""Does the delivered work answer what was actually asked?

The last gap, and the one the other checks cannot close. Evidence proves *something*
was produced; the deliverable rule proves *something* was attached. Neither can tell
whether the something is the thing the person wanted.

The failure this exists for, in full: a person filed **"Check out SEO on
redarchlabs.com and tell me what you think."** Four levels of delegation each restated
it slightly more abstractly — audit the site → run a crawl → design a crawler → write
up the crawler design — until the work being done was crawler architecture. An
adversarial review board of three agents read that design and argued about render
completeness and threat models for four rounds. Every one of them reviewed the design
on its own terms. Not one asked whether anybody wanted a crawler. Nine steps closed
green and the website was never opened.

Nothing in the chain was lying, and no single hop was unreasonable. That is exactly why
a reviewer inside the chain cannot catch it: each agent evaluates against the brief it
was handed, and the brief is what drifted.

So this auditor is deliberately built to be uncontaminated:

* **It reads the original order only** — the title and body as the person typed them,
  which is the one artifact in the whole system that never changed. Not the task list
  (re-planned four times), not the delegation briefs (drifted at every hop).
* **It reads the result, not the reasoning.** The attached artifacts and the closing
  report — what a person opening the order would see. Never the transcript. A reviewer
  that reads the author's reasoning adopts it, which is the observed failure mode of
  the existing review board and the reason it passed a design nobody asked for.
* **It answers one question**, with no lens of its own to defend: *would the person who
  filed this consider it answered?*

It fails **open**. An auditor that cannot run must not freeze every order in the
system, and a wrong refusal costs real work — so an unconfigured key, a model error, or
an unparseable answer all let the transition through and say so in the diary.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.work_order import WorkOrder, WorkOrderArtifact, WorkOrderEntry
from api.services.agents.llm.catalog import provider_for_model
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMError, LLMProvider

logger = logging.getLogger(__name__)

# How much of the closing report the auditor reads. The result is usually the last
# thing said; a long order's early diary is process, not product.
_REPORT_ENTRIES = 6
_REPORT_CHARS = 4_000

# Entry prefixes that are the machine talking about the order rather than anyone
# delivering anything. Feeding these to the auditor invites it to grade the process.
_MACHINE_PREFIXES = ("⚠️", "⛔", "✅", "🏛️", "📎")

_VERDICT = re.compile(r"\b(PASS|FAIL)\b")

_SYSTEM = (
    "You are the acceptance auditor for a work order. You see only two things: what a "
    "person asked for, and what was delivered. You do not see how the work was done, and "
    "you must not infer that unseen effort makes an unrelated deliverable acceptable.\n\n"
    "Answer one question: would the person who filed this request consider it answered?\n\n"
    "Judge only fidelity to the request — not quality, rigour, style or ambition. A "
    "brilliant document that answers a different question is a FAIL. A short, plain answer "
    "that addresses what was asked is a PASS. The most common real failure is a chain of "
    "agents drifting from the request into adjacent work they were better equipped to do: "
    "asked to inspect something, they design a tool for inspecting it; asked for findings, "
    "they deliver a methodology. Treat 'here is how one would do it' as a FAIL when the "
    "request asked for the thing done.\n\n"
    "Reply with exactly one line:\n"
    "PASS\n"
    "or\n"
    "FAIL — <one sentence naming what was asked for and what arrived instead>"
)


@dataclass(frozen=True)
class Verdict:
    """``ok`` is the only thing that gates. ``gap`` is what a person is shown."""

    ok: bool
    gap: str = ""
    #: False when the auditor did not actually run (no key, error, bad reply). The
    #: caller records this so a silent skip is never mistaken for a pass.
    checked: bool = True


async def _artifact_lines(session: AsyncSession, org_id: uuid.UUID, wo_id: uuid.UUID) -> list[str]:
    rows = (
        (
            await session.execute(
                select(WorkOrderArtifact).where(
                    WorkOrderArtifact.org_id == org_id,
                    WorkOrderArtifact.work_order_id == wo_id,
                    WorkOrderArtifact.kind == "output",
                )
            )
        )
        .scalars()
        .all()
    )
    return [f"- {a.filename or a.document_id} ({a.mime or 'document'})" for a in rows]


async def _closing_report(session: AsyncSession, org_id: uuid.UUID, wo_id: uuid.UUID) -> str:
    """What the agents said they delivered, newest last.

    Machine notices are filtered out: the auditor is judging a deliverable, and a
    diary full of the platform's own bookkeeping reads as activity, which is the
    illusion this whole check exists to see through.
    """
    rows = (
        (
            await session.execute(
                select(WorkOrderEntry)
                .where(WorkOrderEntry.work_order_id == wo_id, WorkOrderEntry.org_id == org_id)
                .order_by(WorkOrderEntry.created_at.desc(), WorkOrderEntry.id.desc())
                .limit(_REPORT_ENTRIES * 3)
            )
        )
        .scalars()
        .all()
    )
    said = [
        f"{r.role}: {r.text.strip()}"
        for r in rows
        if r.text.strip() and not r.text.lstrip().startswith(_MACHINE_PREFIXES)
    ]
    return "\n\n".join(reversed(said[:_REPORT_ENTRIES]))[:_REPORT_CHARS]


def _parse(reply: str) -> Verdict:
    """First PASS/FAIL token wins — a model that reasons before answering puts its
    verdict last, and a model that answers as instructed puts it first; either way
    the token is unambiguous. No token at all is a non-answer, not a failure."""
    found = _VERDICT.search(reply or "")
    if found is None:
        return Verdict(ok=True, gap="", checked=False)
    if found.group(1) == "PASS":
        return Verdict(ok=True)
    gap = (reply[found.end() :] or "").strip(" —-:\n") or "the delivered work does not answer the request"
    return Verdict(ok=False, gap=gap.splitlines()[0][:500])


async def check_acceptance(
    session: AsyncSession,
    org_id: uuid.UUID,
    wo: WorkOrder,
    settings: Settings,
    *,
    model: str | None = None,
) -> Verdict:
    """Would the person who filed this consider it answered?"""
    chosen = model or settings.agent_acceptance_model
    provider = provider_for_model(chosen)
    key = await resolve_provider_key(session, org_id, provider, settings)
    if not key:
        logger.info("acceptance check skipped for %s: no key for %s", wo.id, provider)
        return Verdict(ok=True, checked=False)

    artifacts = await _artifact_lines(session, org_id, wo.id)
    report = await _closing_report(session, org_id, wo.id)
    body = (wo.body or "").strip()
    delivered = "\n".join(
        [
            "ATTACHED FILES:",
            "\n".join(artifacts) if artifacts else "- (nothing attached)",
            "",
            "WHAT THE AGENTS REPORTED DELIVERING:",
            report or "(nothing reported)",
        ]
    )
    user = (
        f"THE REQUEST, exactly as the person wrote it:\n{wo.title}\n{body}\n\n----\n\nWHAT WAS DELIVERED:\n{delivered}"
    )

    try:
        result = await LLMProvider(api_key=key).complete(
            model=chosen,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            max_tokens=300,
        )
    except LLMError:
        # Fail open, loudly. Freezing every order in the org because one model call
        # failed would be a far worse outage than letting one questionable order close.
        logger.warning("acceptance check failed to run for work order %s", wo.id, exc_info=True)
        return Verdict(ok=True, checked=False)
    return _parse(result.content or "")
