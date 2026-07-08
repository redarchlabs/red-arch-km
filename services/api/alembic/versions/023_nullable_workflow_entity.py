"""workflows: make entity_definition_id nullable (manual/on-demand workflows).

A workflow whose trigger is a BPMN "none" start event (``data.source == "manual"``)
runs on demand with caller-supplied input variables and is NOT bound to an
entity's create/update/delete stream, so its ``entity_definition_id`` is NULL.
Data-change and form-source triggers still carry an entity.

Revision ID: 023
Revises: 022
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "workflows",
        "entity_definition_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Manual workflows have a NULL entity; they must be removed before the column
    # can be made NOT NULL again.
    op.execute("DELETE FROM workflows WHERE entity_definition_id IS NULL")
    op.alter_column(
        "workflows",
        "entity_definition_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
