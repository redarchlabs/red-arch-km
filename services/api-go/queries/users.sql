-- name: GetUserProfile :one
SELECT * FROM user_profiles WHERE id = $1;

-- name: GetUserProfileByKeycloakSub :one
SELECT * FROM user_profiles WHERE keycloak_sub = $1;

-- name: GetUserProfileByEmail :one
SELECT * FROM user_profiles WHERE email = $1;

-- name: CreateUserProfile :one
INSERT INTO user_profiles (id, keycloak_sub, username, email, description, is_site_admin)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING *;

-- name: UpdateUserProfile :one
UPDATE user_profiles SET
    username = COALESCE(sqlc.narg('username'), username),
    email = COALESCE(sqlc.narg('email'), email),
    description = COALESCE(sqlc.narg('description'), description),
    is_site_admin = COALESCE(sqlc.narg('is_site_admin'), is_site_admin),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: UpsertUserProfile :one
INSERT INTO user_profiles (id, keycloak_sub, username, email, description, is_site_admin)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (keycloak_sub) DO UPDATE SET
    username = EXCLUDED.username,
    email = EXCLUDED.email,
    updated_at = NOW()
RETURNING *;
