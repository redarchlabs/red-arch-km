"""Anchoring relative-time phrases in retrieval queries to concrete dates.

Embedding models have no clock: "the lesson for this week" ranks passages that
contain the words "this week" (calendar boilerplate) above the passage for the
actual current week. Rewriting the phrase inline — "the lesson for the week of
July 27–August 2, 2026" — matches how documents actually state date ranges.

Used by the agentic passage tool and by vector_search (standard chat, search,
workflow knowledge_search). Only unambiguous temporal phrases are rewritten;
words like "current" are left alone ("current password policy" must not grow a
week range).
"""

from __future__ import annotations

import re
from datetime import datetime


def current_week_range(now: datetime | None = None) -> str:
    """The current Monday–Sunday span, e.g. "July 27–August 2"."""
    moment = (now or datetime.now()).astimezone()
    monday = moment.date().fromordinal(moment.date().toordinal() - moment.weekday())
    sunday = monday.fromordinal(monday.toordinal() + 6)
    return f"{monday.strftime('%B')} {monday.day}–{sunday.strftime('%B')} {sunday.day}"


def resolve_relative_dates(query: str, now: datetime | None = None) -> str:
    """Rewrite unambiguous relative-time phrases in ``query`` to concrete dates."""
    moment = (now or datetime.now()).astimezone()
    week = f"the week of {current_week_range(moment)}, {moment.year}"
    day = f"{moment.strftime('%B')} {moment.day}, {moment.year}"
    replacements = [
        (r"\bthis week\b", week),
        (r"\btoday\b", day),
        (r"\bright now\b", f"on {day}"),
        (r"\bthis month\b", f"{moment.strftime('%B')} {moment.year}"),
    ]
    out = query
    for pattern, concrete in replacements:
        out = re.sub(pattern, concrete, out, flags=re.IGNORECASE)
    return out
