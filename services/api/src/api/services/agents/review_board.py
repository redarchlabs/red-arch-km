"""Peer review — who sits on a board, what they decided, and when it is settled.

About a tenth of what an agent produces is confident and wrong, and it looks
exactly like the nine tenths that are fine. An agent cannot catch that in its own
output; a reviewer with a different lens often can. So a written commitment — a
plan, or a finished deliverable — is read by a small board before a person is asked
to approve it, and the person then sees the objections rather than being the first
to look.

Kept **pure** (no session, no I/O) so the rules are unit-testable on their own and
so the two gates that use them — ``submit_plan`` and ``request_review`` — cannot
drift apart. State lives in the work-order diary as marker lines rather than in a
new table, which also means the verdicts render in the diary that already exists.

Ported from the reference implementation in ``~/github/agents_tool``
(``src/design-review.ts``), including two rules worth restating:

* **The author is never on its own board.** Writer ≠ reviewer is the property that
  makes a review mean anything.
* **FAIL dominates PASS** in an ambiguous verdict. A gate must not open on
  "passes, except…".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

# ------------------------------------------------------------------ #
# Vocabulary
# ------------------------------------------------------------------ #

# How many reviewers each level convenes. `standard` is the default: the
# adversarial lens plus one domain lens catches most confident-wrong output for two
# cheap runs. `full` is for an order where being wrong is expensive.
LEVEL_SIZES: dict[str, int] = {"none": 0, "light": 1, "standard": 2, "full": 4}
REVIEW_LEVELS = tuple(LEVEL_SIZES)

# Which board a gate draws from. Business work needs different lenses from
# engineering work — "do the numbers hold" is not a question a security analyst
# answers — so the discipline picks the board and the level picks how many of it.
DEFAULT_BOARD = "engineering"

# After this many failed rounds the work goes to the human anyway, carrying the
# outstanding objections. Without a cap, a reviewer that never softens and an
# author that never satisfies it would trade runs forever.
MAX_ROUNDS = 2

# Diary markers. Text rather than columns, so the whole review history is readable
# in the diary a person already scrolls, and so adding a gate needs no migration.
CONVENED = "[[REVIEW-CONVENED]]"
VERDICT = "[[REVIEW]]"
PASSED = "[[REVIEW-PASSED]]"
RELEASED = "[[REVIEW-RELEASED]]"  # the round cap let it through with objections

PASS = "PASS"  # noqa: S105 - a verdict token, not a credential
FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Seat:
    """One reviewer and the question they are being asked.

    The lens travels with the seat rather than living on the agent, so the same
    agent can be a different reviewer on different boards — and so a board is
    configuration an org can edit rather than a role hierarchy.
    """

    agent: str
    lens: str


@dataclass(frozen=True, slots=True)
class BoardOutcome:
    """Where a convened board stands right now."""

    verdicts: dict[str, str]
    pending: list[str]
    failed: list[str]

    @property
    def settled(self) -> bool:
        """Every seat has reported. Until then the author's run stays parked."""
        return not self.pending

    @property
    def approved(self) -> bool:
        return self.settled and not self.failed


# ------------------------------------------------------------------ #
# Choosing the board
# ------------------------------------------------------------------ #


def _seats(raw: Any) -> list[Seat]:
    out: list[Seat] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent") or "").strip()
        if agent:
            out.append(Seat(agent=agent, lens=str(item.get("lens") or "").strip()))
    return out


def resolve_board(
    boards: Any,
    *,
    level: str,
    board_name: str = DEFAULT_BOARD,
    author: str | None = None,
) -> list[Seat]:
    """The seats to convene for one gate.

    Takes the first ``LEVEL_SIZES[level]`` seats of the named board, *after*
    removing the author — so dropping yourself never silently shrinks the board
    below the size that was asked for.
    """
    size = LEVEL_SIZES.get(level, LEVEL_SIZES["standard"])
    if size == 0:
        return []
    table = boards if isinstance(boards, dict) else {}
    seats = _seats(table.get(board_name) or table.get(DEFAULT_BOARD))
    if author:
        seats = [s for s in seats if s.agent != author]
    return seats[:size]


# ------------------------------------------------------------------ #
# Reading the diary back
# ------------------------------------------------------------------ #


def _text(entry: Any) -> str:
    value = getattr(entry, "text", None)
    return value if isinstance(value, str) else ""


def fingerprint(text: str) -> str:
    """A short digest of what was submitted.

    Resubmitting an unchanged plan must not reconvene the board — that is a wasted
    round every time an agent retries, and the reviewers would have nothing new to
    read.
    """
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]


def convene_marker(gate: str, digest: str, seats: list[Seat]) -> str:
    return f"🏛️ {CONVENED} {gate} ({digest}) — {', '.join(s.agent for s in seats)}"


def verdict_marker(reviewer: str, verdict: str, note: str) -> str:
    return f"🏛️ {VERDICT} {verdict} by {reviewer}: {note}"


def parse_verdict(text: str) -> str:
    """The verdict, taken from the line the reviewer declared it on.

    Reviewers are told to open with PASS or FAIL on its own first line, and then
    write findings — so the verdict is a property of that line, not of the whole
    answer. Scanning the whole answer read a security reviewer's PASS as a FAIL
    the moment its findings used the word "fail" in a sentence, which is most of
    the time. That is not a conservative failure: it discards a real approval and
    sends the author back to fix nothing.

    Within the declaring line FAIL still dominates, so "PASS, though it would FAIL
    under load" does not open the gate. An answer that never declares either is
    treated as a FAIL: a reviewer that would not say is not an approval.
    """
    for line in text.splitlines():
        if re.search(r"\bFAIL\b", line, re.IGNORECASE):
            return FAIL
        if re.search(r"\bPASS\b", line, re.IGNORECASE):
            return PASS
    return FAIL


def rounds_run(entries: list[Any], gate: str) -> int:
    """How many times this gate has convened a board on this order."""
    return sum(1 for e in entries if CONVENED in _text(e) and f"{CONVENED} {gate} " in _text(e))


def last_digest(entries: list[Any], gate: str) -> str | None:
    """The fingerprint of the submission the board last reviewed, if any."""
    for entry in reversed(entries):
        text = _text(entry)
        if f"{CONVENED} {gate} " not in text:
            continue
        match = re.search(r"\(([0-9a-f]{16})\)", text)
        if match:
            return match.group(1)
    return None


def _current_seats(entries: list[Any], gate: str) -> list[str]:
    """The seats named by the most recent convening of this gate.

    Read from the diary rather than recomputed, so a board mid-flight is not
    silently resized by someone editing the org's config underneath it.
    """
    for entry in reversed(entries):
        text = _text(entry)
        if f"{CONVENED} {gate} " not in text:
            continue
        _, _, tail = text.partition("—")
        return [name.strip() for name in tail.split(",") if name.strip()]
    return []


def outcome(entries: list[Any], gate: str) -> BoardOutcome:
    """Where the current round stands.

    Only verdicts filed *after* the latest convening count, so an earlier round's
    PASS cannot satisfy a board looking at a revised plan.
    """
    seats = _current_seats(entries, gate)
    if not seats:
        return BoardOutcome(verdicts={}, pending=[], failed=[])

    started = 0
    for index, entry in enumerate(entries):
        if f"{CONVENED} {gate} " in _text(entry):
            started = index

    verdicts: dict[str, str] = {}
    for entry in entries[started + 1 :]:
        text = _text(entry)
        if VERDICT not in text:
            continue
        match = re.search(r"\b(PASS|FAIL)\b\s+by\s+([A-Za-z0-9_\-]+)", text)
        if not match:
            continue
        who = match.group(2)
        if who in seats:
            # Latest wins, so a reviewer can flip FAIL→PASS once the author fixes it.
            verdicts[who] = match.group(1).upper()

    return BoardOutcome(
        verdicts=verdicts,
        pending=[s for s in seats if s not in verdicts],
        failed=[s for s, v in verdicts.items() if v == FAIL],
    )


def has_passed(entries: list[Any], gate: str, digest: str) -> bool:
    """Has *this* submission already cleared the gate?

    Keyed by digest so a passed gate does not bless a later, different submission.
    """
    needle = f"{PASSED} {gate} ({digest})"
    release = f"{RELEASED} {gate} ({digest})"
    return any(needle in _text(e) or release in _text(e) for e in entries)


def review_brief(*, gate: str, seat: Seat, work_order: str, submission: str, tasks: str) -> str:
    """What a reviewer is given.

    Deliberately just the work order, the submission and the task list — never the
    author's research transcript. A reviewer that reads the author's reasoning
    tends to adopt it, which is the opposite of why it is here, and the transcript
    is the expensive part of the prompt.
    """
    return (
        f"You are reviewing work on the work order “{work_order}”, as one of a small board.\n\n"
        f"YOUR LENS — answer this and nothing else:\n{seat.lens}\n\n"
        f"WHAT IS BEING SUBMITTED ({gate}):\n{submission}\n\n"
        f"THE TASK LIST:\n{tasks or '(none)'}\n\n"
        "Reply with reply_to_peer. Begin your answer with PASS or FAIL on its own first line, "
        "then your findings — be specific and cite what you are objecting to. "
        "FAIL only for something that would actually cause harm or rework; a preference is not a FAIL. "
        "If you FAIL it, say plainly what would have to change to make it a PASS."
    )
