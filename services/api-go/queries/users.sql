-- name: GetUserProfile :one
SELECT * FROM user_profiles WHERE id = $1;

-- name: GetUserProfileByAuthSubject :one
SELECT * FROM user_profiles WHERE auth_subject = $1;

-- name: GetUserProfileByEmail :one
SELECT * FROM user_profiles WHERE email = $1;

-- name: CreateUserProfile :one
INSERT INTO user_profiles (id, auth_subject, username, email, description, is_site_admin)
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
INSERT INTO user_profiles (id, auth_subject, username, email, description, is_site_admin)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (auth_subject) DO UPDATE SET
    username = EXCLUDED.username,
    email = EXCLUDED.email,
    updated_at = NOW()
RETURNING *;

-- name: RelinkAuthSubject :one
-- Rebind an existing profile's auth_subject to a new IdP subject (verified-email
-- relink on first Clerk login). Keys on the internal id, so memberships and the
-- access_mask are preserved. ONLY auth_subject changes — username/email are
-- left untouched to avoid colliding with their UNIQUE constraints.
UPDATE user_profiles SET
    auth_subject = $2,
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: ListUsersInOrg :many
SELECT up.* FROM user_profiles up
JOIN user_org_memberships m ON m.profile_id = up.id
WHERE m.org_id = $1
ORDER BY up.username
LIMIT $2 OFFSET $3;

-- name: CountUsersInOrg :one
SELECT COUNT(*) FROM user_profiles up
JOIN user_org_memberships m ON m.profile_id = up.id
WHERE m.org_id = $1;
