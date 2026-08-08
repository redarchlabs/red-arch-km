"""CLI for the Agent Office import.

The import itself lives in ``api.services.agents.office_import`` so it can be
driven from a test (or any future API surface) with a caller-supplied session;
this file is only the command-line wrapper around it.

Usage (from services/api):
    DATABASE_URL=postgresql+asyncpg://… python -m scripts.import_agent_office
    DATABASE_URL=… python -m scripts.import_agent_office --org "My Org" --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from api.config import get_settings
from api.db import get_session_factory
from api.services.agents.office_import import (
    DEFAULT_ORG,
    KIND_MAP,
    SEEDS,
    import_into,
    load_roster,
)

logger = logging.getLogger("import-agent-office")


async def run(org_name: str, dry_run: bool) -> None:
    roster = load_roster()

    if dry_run:
        kinds: dict[str, int] = {}
        for entry in roster:
            kind = KIND_MAP.get(str(entry.get("kind") or ""), "operator")
            kinds[kind] = kinds.get(kind, 0) + 1
        apex = [e["name"] for e in roster if not e.get("supervisor")]
        scheduled = sum(len(e.get("schedules") or []) for e in roster)
        docs = len(list((SEEDS / "docs").glob("*.md")))
        logger.info("would import %d agents into %r: %s", len(roster), org_name, kinds)
        logger.info("apex (reports to the human): %s", ", ".join(apex) or "none")
        logger.info("schedules: %d (imported disabled)   charter documents: %d", scheduled, docs)
        return

    factory = get_session_factory(get_settings())
    async with factory() as session:
        result = await import_into(session, org_name, roster)
        await session.commit()

    logger.info(
        "imported %d agents (%d reporting lines), %d schedules, %d charter documents into %r",
        result["agents"],
        result["reporting_lines"],
        result["schedules"],
        result["documents"],
        org_name,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Import the Agent Office roster + charter into KM2.")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"Target org name (default: {DEFAULT_ORG!r}).")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be imported; touch nothing.")
    args = parser.parse_args()
    asyncio.run(run(args.org, args.dry_run))


if __name__ == "__main__":
    main()
