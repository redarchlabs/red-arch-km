-- Rollback initial schema

-- Drop RLS policies first
DO $$
DECLARE
    tbl TEXT;
    pol TEXT;
BEGIN
    FOR tbl IN SELECT unnest(ARRAY[
        'regions', 'departments', 'roles', 'groups', 'folders', 'tags',
        'documents', 'document_access', 'document_attribute_definitions',
        'chat_sessions', 'user_org_memberships'
    ])
    LOOP
        FOR pol IN SELECT unnest(ARRAY['select', 'insert', 'update', 'delete'])
        LOOP
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_%s ON %I', pol, tbl);
        END LOOP;
        EXECUTE format('ALTER TABLE IF EXISTS %I DISABLE ROW LEVEL SECURITY', tbl);
    END LOOP;
END $$;

-- Drop tables in dependency-safe order (dependents first)
DROP TABLE IF EXISTS document_tags;
DROP TABLE IF EXISTS document_access;
DROP TABLE IF EXISTS document_attribute_definitions;
DROP TABLE IF EXISTS chat_sessions;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS tags;
DROP TABLE IF EXISTS folders;
DROP TABLE IF EXISTS membership_regions;
DROP TABLE IF EXISTS membership_departments;
DROP TABLE IF EXISTS membership_roles;
DROP TABLE IF EXISTS membership_groups;
DROP TABLE IF EXISTS user_org_memberships;
DROP TABLE IF EXISTS user_profiles;
DROP TABLE IF EXISTS regions;
DROP TABLE IF EXISTS departments;
DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS groups;
DROP TABLE IF EXISTS orgs;
