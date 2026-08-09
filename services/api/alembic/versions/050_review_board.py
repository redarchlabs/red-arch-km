"""Peer review — a committee reads a plan or a deliverable before a person does.

About a tenth of what an agent org produces is confident and wrong, and it looks
exactly like the nine tenths that are fine. An agent cannot catch that in its own
output, and until now the only reviewer was the human at the end.

Three columns, all defaulted so existing behaviour is unchanged:

* ``work_orders.review_level`` — how big a board this order convenes:
  ``none`` | ``light`` (1) | ``standard`` (2) | ``full`` (4). Default ``standard``.
  Per order, because the same roster is worth one adversarial pass on a small job
  and four lenses on a big one.
* ``agents.review_model`` — the model this agent uses **when reviewing**. Reading a
  plan is far cheaper than writing one, so a board can run on a mini model while
  its authors run on something larger. NULL falls back to ``agents.model``.
* ``orgs.review_boards`` — the boards themselves, as ``{name: [{agent, lens}]}``.
  Config rather than code because engineering and business work need different
  lenses, and an org's roster is its own.

Revision ID: 050
Revises: 049
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None

# Seeded so a fresh org has a working committee without anyone authoring JSON.
# Ordered worst-first by general value: `standard` takes the first two, so the
# adversarial lens is always present and the domain lens comes next.
#
# `devils-advocate` is on every board — it is the lens that catches the specific
# failure this feature exists for, an answer that is plausible and wrong.
# The evidence lens is deliberately distinct from it: hallucination is not bad
# judgement, and "is this claim sourced" is not the same question as "is this a
# good idea".
_DEFAULT_BOARDS: dict[str, list[dict[str, str]]] = {
    "engineering": [
        {"agent": "devils-advocate", "lens": "Argue why this is wrong. Attack the weakest assumption."},
        {
            "agent": "security-analyst",
            "lens": "Threat model, data exposure and blast radius. What can this break or leak?",
        },
        {"agent": "principal-engineer", "lens": "Buildability: can this actually be built as described, here?"},
        {
            "agent": "requirements-auditor",
            "lens": "Does this cover what was asked for, and only that? Name scope drift and gaps.",
        },
    ],
    "business": [
        {"agent": "devils-advocate", "lens": "Argue why this is wrong. Attack the weakest assumption."},
        {
            "agent": "research-analyst",
            "lens": (
                "Evidence check. For every factual claim: is it supported by the knowledge base or a "
                "cited source, or is it merely asserted? Name each unsupported claim."
            ),
        },
        {"agent": "financial-analyst", "lens": "Do the numbers hold? Cost, effort and payback."},
        {
            "agent": "requirements-auditor",
            "lens": "Does this cover what was asked for, and only that? Name scope drift and gaps.",
        },
    ],
    "light": [
        {"agent": "devils-advocate", "lens": "Argue why this is wrong. Attack the weakest assumption."},
    ],
}


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column("review_level", sa.String(length=10), nullable=False, server_default="standard"),
    )
    op.add_column("agents", sa.Column("review_model", sa.String(length=120), nullable=True))
    op.add_column(
        "orgs",
        sa.Column("review_boards", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Seeded per org rather than as a column default: a default would be shared by
    # reference, so an org editing its board would be editing everyone's.
    op.get_bind().execute(
        sa.text("UPDATE orgs SET review_boards = CAST(:boards AS jsonb) WHERE review_boards IS NULL"),
        {"boards": json.dumps(_DEFAULT_BOARDS)},
    )


def downgrade() -> None:
    op.drop_column("orgs", "review_boards")
    op.drop_column("agents", "review_model")
    op.drop_column("work_orders", "review_level")
