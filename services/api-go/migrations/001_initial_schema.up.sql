-- Initial schema with Row-Level Security policies
-- Ported from Alembic migration 001_initial_schema_with_rls.py

-- Orgs (no RLS - visible to all authenticated users)
CREATE TABLE orgs (
    id UUID PRIMARY KEY,
    name VARCHAR(200) UNIQUE NOT NULL,
    description TEXT,
    use_knowledge_graph BOOLEAN DEFAULT TRUE,
    openai_api_key VARCHAR(800),
    permission_number SMALLINT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- User Profiles (no RLS - managed by site admins)
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY,
    keycloak_sub VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(150) UNIQUE NOT NULL,
    email VARCHAR(254) UNIQUE NOT NULL,
    description TEXT,
    is_site_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_user_profiles_keycloak_sub ON user_profiles(keycloak_sub);

-- Permission dimension tables
CREATE TABLE regions (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    permission_number SMALLINT DEFAULT 0,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_regions_name_per_org UNIQUE (org_id, name)
);
CREATE INDEX ix_regions_org_id ON regions(org_id);

CREATE TABLE departments (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    permission_number SMALLINT DEFAULT 0,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_departments_name_per_org UNIQUE (org_id, name)
);
CREATE INDEX ix_departments_org_id ON departments(org_id);

CREATE TABLE roles (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    permission_number SMALLINT DEFAULT 0,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_roles_name_per_org UNIQUE (org_id, name)
);
CREATE INDEX ix_roles_org_id ON roles(org_id);

CREATE TABLE groups (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    permission_number SMALLINT DEFAULT 0,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_groups_name_per_org UNIQUE (org_id, name)
);
CREATE INDEX ix_groups_org_id ON groups(org_id);

-- User Org Memberships
CREATE TABLE user_org_memberships (
    id UUID PRIMARY KEY,
    profile_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    is_org_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_profile_org UNIQUE (profile_id, org_id)
);
CREATE INDEX ix_user_org_memberships_org_id ON user_org_memberships(org_id);

-- Membership dimension junction tables
CREATE TABLE membership_regions (
    membership_id UUID NOT NULL REFERENCES user_org_memberships(id) ON DELETE CASCADE,
    region_id UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    PRIMARY KEY (membership_id, region_id)
);
CREATE INDEX ix_membership_regions_region ON membership_regions(region_id);

CREATE TABLE membership_departments (
    membership_id UUID NOT NULL REFERENCES user_org_memberships(id) ON DELETE CASCADE,
    department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    PRIMARY KEY (membership_id, department_id)
);
CREATE INDEX ix_membership_departments_department ON membership_departments(department_id);

CREATE TABLE membership_roles (
    membership_id UUID NOT NULL REFERENCES user_org_memberships(id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (membership_id, role_id)
);
CREATE INDEX ix_membership_roles_role ON membership_roles(role_id);

CREATE TABLE membership_groups (
    membership_id UUID NOT NULL REFERENCES user_org_memberships(id) ON DELETE CASCADE,
    group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (membership_id, group_id)
);
CREATE INDEX ix_membership_groups_group ON membership_groups(group_id);

-- Folders
CREATE TABLE folders (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    "order" INTEGER DEFAULT 0,
    dot_path TEXT DEFAULT '',
    view_permission_masks BIGINT[] DEFAULT '{}',
    contributor_permission_masks BIGINT[] DEFAULT '{}',
    viewer_permissions_config JSONB,
    contributor_permissions_config JSONB,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES folders(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_folder_name_per_org_parent UNIQUE (org_id, name, parent_id)
);
CREATE INDEX ix_folders_org_id ON folders(org_id);
CREATE INDEX ix_folders_dot_path ON folders(dot_path);

-- Tags
CREATE TABLE tags (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_tag_per_org UNIQUE (org_id, name)
);

-- Documents
CREATE TABLE documents (
    id UUID PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    text TEXT,
    document_key VARCHAR(255) NOT NULL,
    document_url VARCHAR(2048),
    processing_status VARCHAR(20) DEFAULT 'PENDING',
    processing_details JSONB,
    metadata JSONB,
    use_knowledge_graph BOOLEAN,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    folder_id UUID REFERENCES folders(id) ON DELETE SET NULL,
    uploaded_by_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_doc_key_per_org UNIQUE (org_id, document_key)
);
CREATE INDEX ix_documents_org_id ON documents(org_id);
CREATE INDEX ix_documents_folder_id ON documents(folder_id);
CREATE INDEX ix_documents_uploaded_by_id ON documents(uploaded_by_id);

-- Document-Tag M2M
CREATE TABLE document_tags (
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);
CREATE INDEX ix_document_tags_tag_id ON document_tags(tag_id);

-- Document Access
CREATE TABLE document_access (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    folder_id UUID NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_document_access_user_folder UNIQUE (user_id, folder_id)
);
CREATE INDEX ix_document_access_folder_id ON document_access(folder_id);

-- Document Attribute Definitions
CREATE TABLE document_attribute_definitions (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(100) NOT NULL,
    attribute_type VARCHAR(20) DEFAULT 'freeform',
    picklist_options JSONB DEFAULT '[]',
    required BOOLEAN DEFAULT FALSE,
    "order" INTEGER DEFAULT 0,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_attr_slug_per_org UNIQUE (org_id, slug)
);

-- Chat Sessions
CREATE TABLE chat_sessions (
    id UUID PRIMARY KEY,
    chat_data JSONB,
    deleted BOOLEAN DEFAULT FALSE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_chat_sessions_org_id ON chat_sessions(org_id);

-- =============================================================================
-- ROW-LEVEL SECURITY POLICIES
-- Every tenant-scoped table gets RLS enforced via app.current_tenant_id
-- =============================================================================

-- Enable RLS on tenant-scoped tables
ALTER TABLE regions ENABLE ROW LEVEL SECURITY;
ALTER TABLE regions FORCE ROW LEVEL SECURITY;
ALTER TABLE departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE departments FORCE ROW LEVEL SECURITY;
ALTER TABLE roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles FORCE ROW LEVEL SECURITY;
ALTER TABLE groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE groups FORCE ROW LEVEL SECURITY;
ALTER TABLE folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE folders FORCE ROW LEVEL SECURITY;
ALTER TABLE tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE tags FORCE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
ALTER TABLE document_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_access FORCE ROW LEVEL SECURITY;
ALTER TABLE document_attribute_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_attribute_definitions FORCE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE user_org_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_org_memberships FORCE ROW LEVEL SECURITY;

-- Create RLS policies for each tenant-scoped table
-- Policy: rows visible only when org_id matches current_setting

-- regions
CREATE POLICY tenant_isolation_select ON regions FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON regions FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON regions FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON regions FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- departments
CREATE POLICY tenant_isolation_select ON departments FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON departments FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON departments FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON departments FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- roles
CREATE POLICY tenant_isolation_select ON roles FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON roles FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON roles FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON roles FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- groups
CREATE POLICY tenant_isolation_select ON groups FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON groups FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON groups FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON groups FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- folders
CREATE POLICY tenant_isolation_select ON folders FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON folders FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON folders FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON folders FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- tags
CREATE POLICY tenant_isolation_select ON tags FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON tags FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON tags FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON tags FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- documents
CREATE POLICY tenant_isolation_select ON documents FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON documents FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON documents FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON documents FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- document_access
CREATE POLICY tenant_isolation_select ON document_access FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON document_access FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON document_access FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON document_access FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- document_attribute_definitions
CREATE POLICY tenant_isolation_select ON document_attribute_definitions FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON document_attribute_definitions FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON document_attribute_definitions FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON document_attribute_definitions FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- chat_sessions
CREATE POLICY tenant_isolation_select ON chat_sessions FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON chat_sessions FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON chat_sessions FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON chat_sessions FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- user_org_memberships
CREATE POLICY tenant_isolation_select ON user_org_memberships FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_insert ON user_org_memberships FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_update ON user_org_memberships FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
CREATE POLICY tenant_isolation_delete ON user_org_memberships FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
