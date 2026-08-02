"""Date context for LLM prompts.

Models have no clock: without an explicit date in the prompt they cannot
resolve "today", "this week", or "current" — and grounded agents will
(fruitlessly) search the knowledge base for the current date instead.
Every prompt-building site injects this line at request time, never at
import time, so long-lived processes don't freeze the date.
"""

from __future__ import annotations

from datetime import datetime


def current_date_line(now: datetime | None = None) -> str:
    """One sentence stating the current local date, for a system prompt.

    Includes the weekday so the model can reason about "this week", and the
    UTC offset so date boundaries are unambiguous.
    """
    moment = (now or datetime.now()).astimezone()
    return (
        f"The current date is {moment.strftime('%A, %B')} {moment.day}, {moment.year} "
        f"({moment.date().isoformat()}, UTC{moment.strftime('%z')})."
    )
