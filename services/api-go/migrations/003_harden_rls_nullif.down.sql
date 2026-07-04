-- RED-3: Revert RED-3 hardening -- restore the bare cast policies from migration 001.
--
-- The initial policies (001) used a bare cast:
--     org_id = current_setting('app.current_tenant_id', true)::uuid
-- current_setting(name, true) returns '' (empty string), NOT NULL, when the
-- custom GUC has been defined-then-reverted on a pooled connection (after a
-- prior request set the tenant with set_config(..., true) and the txn ended).
-- Casting ''::uuid raises "invalid input syntax for type uuid" on the next RLS
-- query -- fail-closed but a 500 instead of an empty result.
--
-- nullif(current_setting(...), '') normalises '' -> NULL so an unset/empty tenant
-- deterministically returns zero rows (SELECT) and blocks writes (INSERT/UPDATE/
-- DELETE) with no error. RLS stays ENABLED/FORCED throughout; drop+recreate keeps
-- no unprotected window.


-- regions
DROP POLICY IF EXISTS tenant_isolation_select ON regions;
CREATE POLICY tenant_isolation_select ON regions FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON regions;
CREATE POLICY tenant_isolation_insert ON regions FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON regions;
CREATE POLICY tenant_isolation_update ON regions FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON regions;
CREATE POLICY tenant_isolation_delete ON regions FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- departments
DROP POLICY IF EXISTS tenant_isolation_select ON departments;
CREATE POLICY tenant_isolation_select ON departments FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON departments;
CREATE POLICY tenant_isolation_insert ON departments FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON departments;
CREATE POLICY tenant_isolation_update ON departments FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON departments;
CREATE POLICY tenant_isolation_delete ON departments FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- roles
DROP POLICY IF EXISTS tenant_isolation_select ON roles;
CREATE POLICY tenant_isolation_select ON roles FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON roles;
CREATE POLICY tenant_isolation_insert ON roles FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON roles;
CREATE POLICY tenant_isolation_update ON roles FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON roles;
CREATE POLICY tenant_isolation_delete ON roles FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- groups
DROP POLICY IF EXISTS tenant_isolation_select ON groups;
CREATE POLICY tenant_isolation_select ON groups FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON groups;
CREATE POLICY tenant_isolation_insert ON groups FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON groups;
CREATE POLICY tenant_isolation_update ON groups FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON groups;
CREATE POLICY tenant_isolation_delete ON groups FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- folders
DROP POLICY IF EXISTS tenant_isolation_select ON folders;
CREATE POLICY tenant_isolation_select ON folders FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON folders;
CREATE POLICY tenant_isolation_insert ON folders FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON folders;
CREATE POLICY tenant_isolation_update ON folders FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON folders;
CREATE POLICY tenant_isolation_delete ON folders FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- tags
DROP POLICY IF EXISTS tenant_isolation_select ON tags;
CREATE POLICY tenant_isolation_select ON tags FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON tags;
CREATE POLICY tenant_isolation_insert ON tags FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON tags;
CREATE POLICY tenant_isolation_update ON tags FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON tags;
CREATE POLICY tenant_isolation_delete ON tags FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- documents
DROP POLICY IF EXISTS tenant_isolation_select ON documents;
CREATE POLICY tenant_isolation_select ON documents FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON documents;
CREATE POLICY tenant_isolation_insert ON documents FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON documents;
CREATE POLICY tenant_isolation_update ON documents FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON documents;
CREATE POLICY tenant_isolation_delete ON documents FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- document_access
DROP POLICY IF EXISTS tenant_isolation_select ON document_access;
CREATE POLICY tenant_isolation_select ON document_access FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON document_access;
CREATE POLICY tenant_isolation_insert ON document_access FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON document_access;
CREATE POLICY tenant_isolation_update ON document_access FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON document_access;
CREATE POLICY tenant_isolation_delete ON document_access FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- document_attribute_definitions
DROP POLICY IF EXISTS tenant_isolation_select ON document_attribute_definitions;
CREATE POLICY tenant_isolation_select ON document_attribute_definitions FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON document_attribute_definitions;
CREATE POLICY tenant_isolation_insert ON document_attribute_definitions FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON document_attribute_definitions;
CREATE POLICY tenant_isolation_update ON document_attribute_definitions FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON document_attribute_definitions;
CREATE POLICY tenant_isolation_delete ON document_attribute_definitions FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- chat_sessions
DROP POLICY IF EXISTS tenant_isolation_select ON chat_sessions;
CREATE POLICY tenant_isolation_select ON chat_sessions FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON chat_sessions;
CREATE POLICY tenant_isolation_insert ON chat_sessions FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON chat_sessions;
CREATE POLICY tenant_isolation_update ON chat_sessions FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON chat_sessions;
CREATE POLICY tenant_isolation_delete ON chat_sessions FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);

-- user_org_memberships
DROP POLICY IF EXISTS tenant_isolation_select ON user_org_memberships;
CREATE POLICY tenant_isolation_select ON user_org_memberships FOR SELECT
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_insert ON user_org_memberships;
CREATE POLICY tenant_isolation_insert ON user_org_memberships FOR INSERT
    WITH CHECK (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_update ON user_org_memberships;
CREATE POLICY tenant_isolation_update ON user_org_memberships FOR UPDATE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
DROP POLICY IF EXISTS tenant_isolation_delete ON user_org_memberships;
CREATE POLICY tenant_isolation_delete ON user_org_memberships FOR DELETE
    USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
