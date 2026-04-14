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
