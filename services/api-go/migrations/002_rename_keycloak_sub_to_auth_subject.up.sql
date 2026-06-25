-- Rename the IdP-subject column to be provider-neutral (Keycloak -> Clerk, D3).
-- This is a pure rename: the UNIQUE NOT NULL constraint and all foreign keys
-- (which reference user_profiles.id, not this column) are preserved, so
-- memberships and the access_mask are unaffected. Existing rows keep their
-- Keycloak subject in auth_subject until a verified-email relink rebinds it to
-- the Clerk subject on first Clerk login.
-- NOTE: Python parity is deferred (decision A, Go-only Clerk). A future restore
-- of the Python stack must align it to this rename — the Alembic migration, the
-- UserProfile ORM (keycloak_sub -> auth_subject), and services/api/scripts/
-- seed_e2e.py (the e2e seed filters on keycloak_sub) all reference the old name.
ALTER TABLE user_profiles RENAME COLUMN keycloak_sub TO auth_subject;
ALTER INDEX ix_user_profiles_keycloak_sub RENAME TO ix_user_profiles_auth_subject;
