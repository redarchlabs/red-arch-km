"""Per-entity write access + per-field read access (tamper-proof records).

Two catalog policies that let an org lock down a record surface it exposes to
regular members (e.g. a quiz answer key or a certification record):

* ``entity_definitions.write_access`` — ``"member"`` (default: any org member may
  create/update/delete via the record API) or ``"workflow_only"`` (only the
  workflow engine and org admins may write; direct member writes are 403'd).
* ``entity_fields.read_access`` — ``"member"`` (default) or ``"server_only"`` (the
  field's values are stripped from the record API for regular members and cannot
  be filtered/sorted/grouped on — only the workflow engine and org admins see it).

Both default to the pre-existing fully-open behaviour, so existing entities are
unaffected until an admin opts a field/entity into the stricter policy.

Revision ID: 039
Revises: 038
"""

import sqlalchemy as sa
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entity_definitions",
        sa.Column("write_access", sa.String(20), nullable=False, server_default="member"),
    )
    op.add_column(
        "entity_fields",
        sa.Column("read_access", sa.String(20), nullable=False, server_default="member"),
    )


def downgrade() -> None:
    op.drop_column("entity_fields", "read_access")
    op.drop_column("entity_definitions", "write_access")
