-- Reverse the provider-neutral rename (back to keycloak_sub).
ALTER INDEX ix_user_profiles_auth_subject RENAME TO ix_user_profiles_keycloak_sub;
ALTER TABLE user_profiles RENAME COLUMN auth_subject TO keycloak_sub;
