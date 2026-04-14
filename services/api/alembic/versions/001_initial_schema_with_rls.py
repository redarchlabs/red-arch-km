"""Initial schema with Row-Level Security policies.

Revision ID: 001
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

# Tables that require RLS with org_id as tenant column
_RLS_TABLES = [
    "regions",
    "departments",
    "roles",
    "groups",
    "folders",
    "tags",
    "documents",
    "document_access",
    "document_attribute_definitions",
    "chat_sessions",
    "user_org_memberships",
]


def upgrade() -> None:
    # --- Orgs (no RLS - visible to all authenticated users) ---
    op.create_table(
        "orgs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("use_knowledge_graph", sa.Boolean, default=True),
        sa.Column("openai_api_key", sa.String(800), nullable=True),
        sa.Column("permission_number", sa.SmallInteger, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- User Profiles (no RLS - managed by site admins) ---
    op.create_table(
        "user_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("keycloak_sub", sa.String(255), unique=True, index=True, nullable=False),
        sa.Column("username", sa.String(150), unique=True, nullable=False),
        sa.Column("email", sa.String(254), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_site_admin", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- Permission dimension tables ---
    for table_name, related_name in [
        ("regions", "regions"),
        ("departments", "departments"),
        ("roles", "roles"),
        ("groups", "groups"),
    ]:
        op.create_table(
            table_name,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("permission_number", sa.SmallInteger, default=0),
            sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("org_id", "name", name=f"uq_{table_name}_name_per_org"),
        )

    # --- User Org Memberships ---
    op.create_table(
        "user_org_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("is_org_admin", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("profile_id", "org_id", name="uq_profile_org"),
    )

    # Membership M2M junction tables
    # Composite PK on (membership_id, dim_id) prevents duplicate assignments
    # and gives us the best index for reverse lookups ("which members have this role?").
    for dim in ["regions", "departments", "roles", "groups"]:
        singular = dim.rstrip("s") if dim != "groups" else "group"
        op.create_table(
            f"membership_{dim}",
            sa.Column("membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_org_memberships.id", ondelete="CASCADE"), nullable=False),
            sa.Column(f"{singular}_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{dim}.id", ondelete="CASCADE"), nullable=False),
            sa.PrimaryKeyConstraint("membership_id", f"{singular}_id", name=f"pk_membership_{dim}"),
        )
        op.create_index(
            f"ix_membership_{dim}_{singular}",
            f"membership_{dim}",
            [f"{singular}_id"],
        )

    # --- Folders ---
    op.create_table(
        "folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("order", sa.Integer, default=0),
        sa.Column("dot_path", sa.Text, default="", index=True),
        sa.Column("view_permission_masks", postgresql.ARRAY(sa.BigInteger), server_default="{}"),
        sa.Column("contributor_permission_masks", postgresql.ARRAY(sa.BigInteger), server_default="{}"),
        sa.Column("viewer_permissions_config", postgresql.JSONB, nullable=True),
        sa.Column("contributor_permissions_config", postgresql.JSONB, nullable=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "name", "parent_id", name="uq_folder_name_per_org_parent"),
    )

    # --- Tags ---
    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "name", name="uq_tag_per_org"),
    )

    # --- Documents ---
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("text", sa.Text, nullable=True),
        sa.Column("document_key", sa.String(255), nullable=False),
        sa.Column("document_url", sa.String(2048), nullable=True),
        sa.Column("processing_status", sa.String(20), default="PENDING"),
        sa.Column("processing_details", postgresql.JSONB, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("use_knowledge_graph", sa.Boolean, nullable=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "document_key", name="uq_doc_key_per_org"),
    )

    # Document-Tag M2M with composite PK to prevent duplicate tag assignments
    op.create_table(
        "document_tags",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "tag_id", name="pk_document_tags"),
    )
    op.create_index("ix_document_tags_tag_id", "document_tags", ["tag_id"])

    # Secondary indexes on FKs that are queried independently
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])
    op.create_index("ix_documents_uploaded_by_id", "documents", ["uploaded_by_id"])

    # --- Document Access ---
    op.create_table(
        "document_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "folder_id", name="uq_document_access_user_folder"),
    )
    op.create_index("ix_document_access_folder_id", "document_access", ["folder_id"])

    # --- Document Attribute Definitions ---
    op.create_table(
        "document_attribute_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("attribute_type", sa.String(20), default="freeform"),
        sa.Column("picklist_options", postgresql.JSONB, default=[]),
        sa.Column("required", sa.Boolean, default=False),
        sa.Column("order", sa.Integer, default=0),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "slug", name="uq_attr_slug_per_org"),
    )

    # --- Chat Sessions ---
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chat_data", postgresql.JSONB, nullable=True),
        sa.Column("deleted", sa.Boolean, default=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # =========================================================================
    # ROW-LEVEL SECURITY POLICIES
    # Every tenant-scoped table gets RLS enforced via app.current_tenant_id
    # =========================================================================
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        # Policy: rows visible only when org_id matches current_setting
        op.execute(f"""
            CREATE POLICY tenant_isolation_select ON {table}
            FOR SELECT
            USING (org_id = current_setting('app.current_tenant_id', true)::uuid)
        """)

        op.execute(f"""
            CREATE POLICY tenant_isolation_insert ON {table}
            FOR INSERT
            WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid)
        """)

        op.execute(f"""
            CREATE POLICY tenant_isolation_update ON {table}
            FOR UPDATE
            USING (org_id = current_setting('app.current_tenant_id', true)::uuid)
        """)

        op.execute(f"""
            CREATE POLICY tenant_isolation_delete ON {table}
            FOR DELETE
            USING (org_id = current_setting('app.current_tenant_id', true)::uuid)
        """)

    # Grant app_user access (RLS applies)
    for table in _RLS_TABLES + ["orgs", "user_profiles", "document_tags",
                                  "membership_regions", "membership_departments",
                                  "membership_roles", "membership_groups"]:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def downgrade() -> None:
    # Drop RLS policies
    for table in _RLS_TABLES:
        for action in ["select", "insert", "update", "delete"]:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{action} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop tables in dependency-safe order (dependents first, referenced last).
    # Without this order, dropping `orgs` before `regions`/`departments`/etc.
    # raises FK-dependent-objects errors.
    for table in [
        "document_tags",
        "document_access",
        "document_attribute_definitions",
        "chat_sessions",
        "documents",
        "tags",
        "folders",
        "membership_regions",
        "membership_departments",
        "membership_roles",
        "membership_groups",
        "user_org_memberships",
        "user_profiles",
        "regions",
        "departments",
        "roles",
        "groups",
        "orgs",
    ]:
        op.drop_table(table)
