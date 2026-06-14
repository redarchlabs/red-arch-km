-- name: GetOrg :one
SELECT * FROM orgs WHERE id = $1;

-- name: ListOrgs :many
SELECT * FROM orgs ORDER BY name;

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
