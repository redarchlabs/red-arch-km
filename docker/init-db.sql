-- PostgreSQL initialization script
-- Sets up Row-Level Security (RLS) roles for tenant isolation

-- Application role used by the API service
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN PASSWORD 'changeme';
    END IF;
END
$$;

-- Admin role that bypasses RLS (for migrations and site admin operations)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_admin') THEN
        CREATE ROLE app_admin LOGIN PASSWORD 'changeme' BYPASSRLS;
    END IF;
END
$$;

-- Grant usage
GRANT ALL PRIVILEGES ON DATABASE redarch_km TO app_admin;
GRANT CONNECT ON DATABASE redarch_km TO app_user;

-- Runtime application login role (Cobalt pattern). The app connects as THIS
-- role — non-superuser, non-BYPASSRLS — so PostgreSQL RLS is actually enforced
-- (connecting as the superuser, as older configs did, silently masks RLS bugs).
-- Cross-org / no-tenant-context paths opt into visibility via the `app.bypass`
-- GUC + the admin_bypass_all policy (migration 034), NOT via role privileges.
--   * member of app_user  -> inherits the per-table CRUD grants
--   * CREATE on schema     -> lets the entity authoring paths create ce_* tables
-- Migrations still run as the admin/superuser (POSTGRES_USER), which owns the
-- schema. Dev password is fixed; prod (Cloud SQL) sets it via Terraform + a
-- Secret Manager secret and grants the same memberships in a bootstrap step.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'km_app') THEN
        CREATE ROLE km_app LOGIN PASSWORD 'km_app_dev';
    END IF;
END
$$;
GRANT app_user TO km_app;
GRANT CONNECT ON DATABASE redarch_km TO km_app;
GRANT CREATE ON SCHEMA public TO km_app;
