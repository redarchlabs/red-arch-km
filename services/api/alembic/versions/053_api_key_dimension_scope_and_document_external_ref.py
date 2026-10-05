"""API keys bound to dimension assignments; caller-owned document refs; API-originated runs.

Three additions for the public ``/api/v1`` surface:

1. ``api_keys.access_mode`` (``'org'`` | ``'scoped'``, default ``'org'``) and five
   join tables — ``api_key_regions``, ``api_key_roles``, ``api_key_groups``,
   ``api_key_departments``, ``api_key_folders`` — each ``PRIMARY KEY (api_key_id,
   <item>_id)``. A scoped key reads knowledge with the masks of its own dimension
   assignments (exactly as a member holding them would), optionally narrowed to
   the listed folders and their subfolders. Folders only narrow, never grant.

   ``api_key_id`` is ``ON DELETE CASCADE``. The item FK is ``ON DELETE NO
   ACTION DEFERRABLE INITIALLY DEFERRED``: it refuses deleting a role/folder a key
   still holds, but only checks at commit. Deleting an org cascades to its keys
   *and* its roles/folders, and PostgreSQL runs each cascade as its own query and
   checks a non-deferred FK (NO ACTION as much as RESTRICT) at the end of THAT
   query — before the keys' cascade may have run — so anything stricter makes
   ``DELETE FROM orgs`` (and the ORM's role-by-role org delete) fail. The API
   answers such a delete with a 409 naming the active keys, after purging rows of
   revoked/expired keys.

   The join tables carry no ``org_id`` and so no RLS; every id written to them is
   validated against the key's org (tenant session) when the key is minted.

   ``ix_folders_parent_id``: a key's folders expand to their subfolders by walking
   ``parent_id`` (a recursive query) — never by the name-built ``dot_path``, which
   two root folders may share — so that walk needs an index.

   Developers who applied an earlier draft of this revision (with
   ``api_keys.profile_id``) must ``alembic downgrade 052`` before upgrading.

2. ``documents.external_ref`` + ``documents.content_hash`` — a stable id chosen by
   the caller of ``POST /api/v1/knowledge/documents`` and the SHA-256 of the last
   content ingested under it. Re-sending the same ref replaces the document's
   content (a new version of the same row); re-sending identical content is a
   no-op.

   The ref is unique **per folder** (partial index): the same ref in another
   folder is a different document, so a write never reveals what a ref names in a
   folder the caller may not see.

3. ``agent_runs.via_api_key`` / ``api_key_id`` and the same on ``work_orders`` —
   set on runs and orders created through ``/api/v1`` and inherited by every run
   spawned from them. Such a run searches only its own org and never reads
   unrestricted; a scoped key's run is limited to mask-aware tools and its
   writes re-check the key (still active, still holding the scope). A scoped key's
   runs have no actor; every tool call reloads the key and its assignments.
   ``via_api_key`` is a non-null flag on purpose: ``api_key_id`` is SET NULL when a
   key is deleted, and losing the origin must never lift the limits.

Nothing is backfilled: existing keys get ``access_mode='org'`` (today's behaviour),
existing documents have no ref, and existing runs/orders are not API-originated.

Revision ID: 053
Revises: 052
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


_ASSIGNMENT_TABLES = (
    ("api_key_regions", "region_id", "regions"),
    ("api_key_roles", "role_id", "roles"),
    ("api_key_groups", "group_id", "groups"),
    ("api_key_departments", "department_id", "departments"),
    ("api_key_folders", "folder_id", "folders"),
)


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("access_mode", sa.String(10), nullable=False, server_default="org"),
    )
    op.create_check_constraint("ck_api_keys_access_mode", "api_keys", "access_mode IN ('org', 'scoped')")
    for table, column, target in _ASSIGNMENT_TABLES:
        op.create_table(
            table,
            sa.Column(
                "api_key_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            # NO ACTION, deferred to commit, so an org delete cascading to both
            # sides still succeeds (see the module docstring).
            sa.Column(
                column,
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(f"{target}.id", deferrable=True, initially="DEFERRED"),
                primary_key=True,
            ),
        )
        # Reverse lookups: "which keys hold this role/folder?" on delete.
        op.create_index(f"ix_{table}_{column}", table, [column])
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")

    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])

    op.add_column("documents", sa.Column("external_ref", sa.String(255), nullable=True))
    op.add_column("documents", sa.Column("content_hash", sa.String(64), nullable=True))
    op.create_index(
        "uq_doc_external_ref_per_folder",
        "documents",
        ["org_id", "folder_id", "external_ref"],
        unique=True,
        postgresql_where=sa.text("external_ref IS NOT NULL"),
    )

    for table in ("agent_runs", "work_orders"):
        op.add_column(table, sa.Column("via_api_key", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column(
            table,
            sa.Column(
                "api_key_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        # A scoped key lists only its own runs and work orders.
        op.create_index(f"ix_{table}_api_key_id", table, ["api_key_id"])


def downgrade() -> None:
    for table in ("work_orders", "agent_runs"):
        op.drop_index(f"ix_{table}_api_key_id", table_name=table)
        op.drop_column(table, "api_key_id")
        op.drop_column(table, "via_api_key")
    op.drop_index("uq_doc_external_ref_per_folder", table_name="documents")
    op.drop_column("documents", "content_hash")
    op.drop_column("documents", "external_ref")
    op.drop_index("ix_folders_parent_id", table_name="folders")
    for table, column, _target in reversed(_ASSIGNMENT_TABLES):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
    op.drop_constraint("ck_api_keys_access_mode", "api_keys", type_="check")
    op.drop_column("api_keys", "access_mode")
