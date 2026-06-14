-- name: GetOrg :one
SELECT * FROM orgs WHERE id = $1;

-- name: ListAllOrgs :many
SELECT * FROM orgs ORDER BY name LIMIT $1 OFFSET $2;

-- name: CountAllOrgs :one
SELECT COUNT(*) FROM orgs;

-- name: ListOrgsForUser :many
SELECT o.* FROM orgs o
JOIN user_org_memberships m ON m.org_id = o.id
WHERE m.profile_id = $1
ORDER BY o.name
LIMIT $2 OFFSET $3;

-- name: CountOrgsForUser :one
SELECT COUNT(*) FROM orgs o
JOIN user_org_memberships m ON m.org_id = o.id
WHERE m.profile_id = $1;

-- name: GetNextOrgPermissionNumber :one
SELECT COALESCE(MAX(permission_number), 0) + 1 FROM orgs FOR UPDATE;

-- name: CreateOrg :one
INSERT INTO orgs (id, name, description, use_knowledge_graph, permission_number)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: UpdateOrg :one
UPDATE orgs SET
    name = COALESCE(sqlc.narg('name'), name),
    description = COALESCE(sqlc.narg('description'), description),
    use_knowledge_graph = COALESCE(sqlc.narg('use_knowledge_graph'), use_knowledge_graph),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteOrg :exec
DELETE FROM orgs WHERE id = $1;

-- name: IsUserMemberOfOrg :one
SELECT EXISTS(
    SELECT 1 FROM user_org_memberships
    WHERE profile_id = $1 AND org_id = $2
) AS is_member;
