-- name: GetTag :one
SELECT * FROM tags WHERE id = $1;

-- name: GetTagByName :one
SELECT * FROM tags WHERE name = $1 AND org_id = $2;

-- name: ListTags :many
SELECT * FROM tags ORDER BY name LIMIT $1 OFFSET $2;

-- name: ListTagsForOrg :many
SELECT * FROM tags WHERE org_id = $1 ORDER BY name LIMIT $2 OFFSET $3;

-- name: CountTags :one
SELECT COUNT(*) FROM tags;

-- name: CountTagsForOrg :one
SELECT COUNT(*) FROM tags WHERE org_id = $1;

-- name: CreateTag :one
INSERT INTO tags (id, name, org_id)
VALUES ($1, $2, $3)
RETURNING *;

-- name: UpdateTag :one
UPDATE tags SET
    name = COALESCE(sqlc.narg('name'), name),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteTag :exec
DELETE FROM tags WHERE id = $1;

-- name: GetTagsByIDs :many
SELECT * FROM tags WHERE id = ANY($1::uuid[]);

-- name: ListTagsForDocument :many
SELECT t.* FROM tags t
JOIN document_tags dt ON dt.tag_id = t.id
WHERE dt.document_id = $1
ORDER BY t.name;
