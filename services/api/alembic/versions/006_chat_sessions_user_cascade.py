"""Cascade-delete chat_sessions with their owning user; make user_id NOT NULL.

chat_sessions.user_id originally used ON DELETE SET NULL, which *orphaned* a
user's private conversations when their profile was deleted instead of removing
them. Orphaned rows (user_id IS NULL) are unreachable — list_for_user filters by
user_id, so no one can ever see them again. Switch to ON DELETE CASCADE and make
user_id NOT NULL: every session is created with an owner, and a user-less session
is invalid by design.

Existing orphaned rows are deleted so the NOT NULL constraint can be applied.
chat_sessions has FORCE row-level security; a migration runs without a tenant
GUC, so the tenant-isolation policy would match zero rows and silently skip the
cleanup. The DELETE is therefore bracketed by DISABLE/ENABLE + FORCE RLS to
restore the table to its original security posture afterward.

Revision ID: 006
Revises: 005
Create Date: 2026-07-05
"""

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

# Postgres auto-names a single-column inline FK "<table>_<column>_fkey".
_FK = "chat_sessions_user_id_fkey"


def upgrade() -> None:
    # Purge unreachable orphaned rows. RLS is FORCEd, so bypass tenant
    # isolation for the cleanup, then restore the original RLS posture.
    op.execute("ALTER TABLE chat_sessions DISABLE ROW LEVEL SECURITY")
    op.execute("DELETE FROM chat_sessions WHERE user_id IS NULL")
    op.execute("ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_sessions FORCE ROW LEVEL SECURITY")

    op.drop_constraint(_FK, "chat_sessions", type_="foreignkey")
    op.alter_column("chat_sessions", "user_id", nullable=False)
    op.create_foreign_key(
        _FK,
        "chat_sessions",
        "user_profiles",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "chat_sessions", type_="foreignkey")
    op.alter_column("chat_sessions", "user_id", nullable=True)
    op.create_foreign_key(
        _FK,
        "chat_sessions",
        "user_profiles",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
