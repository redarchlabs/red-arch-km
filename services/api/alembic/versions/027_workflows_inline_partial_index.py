"""workflows: partial index for the inline-dispatch hot path.

``has_inline_for_entity`` runs an EXISTS on EVERY record create/update/delete
(filtering org_id + entity_definition_id + enabled + run_inline_on_change). Only
single-column indexes on org_id / entity_definition_id existed, so the check's
cost grew with the workflow count. This partial index makes it an index-only
lookup that stays flat, and — being partial — costs almost nothing to maintain
(only inline-flagged rows are indexed).

Revision ID: 027
Revises: 026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_workflows_inline_entity",
        "workflows",
        ["org_id", "entity_definition_id"],
        unique=False,
        postgresql_where=sa.text("enabled AND run_inline_on_change"),
    )


def downgrade() -> None:
    op.drop_index("ix_workflows_inline_entity", table_name="workflows")
