-- name: GetFolder :one
SELECT * FROM folders WHERE id = $1;

-- name: GetFolderByName :one
SELECT * FROM folders WHERE name = $1 AND org_id = $2 AND parent_id IS NOT DISTINCT FROM $3;

-- name: ListFolders :many
SELECT * FROM folders ORDER BY dot_path, "order", name LIMIT $1 OFFSET $2;

-- name: ListFoldersForOrg :many
SELECT * FROM folders WHERE org_id = $1 ORDER BY dot_path, "order", name LIMIT $2 OFFSET $3;

-- name: CountFolders :one
SELECT COUNT(*) FROM folders;

-- name: CountFoldersForOrg :one
SELECT COUNT(*) FROM folders WHERE org_id = $1;

-- name: CreateFolder :one
INSERT INTO folders (
    id, name, description, "order", dot_path,
    view_permission_masks, contributor_permission_masks,
    viewer_permissions_config, contributor_permissions_config,
    org_id, parent_id
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
RETURNING *;

-- name: UpdateFolder :one
UPDATE folders SET
    name = COALESCE(sqlc.narg('name'), name),
    description = COALESCE(sqlc.narg('description'), description),
    "order" = COALESCE(sqlc.narg('order'), "order"),
    dot_path = COALESCE(sqlc.narg('dot_path'), dot_path),
    view_permission_masks = COALESCE(sqlc.narg('view_permission_masks'), view_permission_masks),
    contributor_permission_masks = COALESCE(sqlc.narg('contributor_permission_masks'), contributor_permission_masks),
    viewer_permissions_config = COALESCE(sqlc.narg('viewer_permissions_config'), viewer_permissions_config),
    contributor_permissions_config = COALESCE(sqlc.narg('contributor_permissions_config'), contributor_permissions_config),
    parent_id = CASE
        WHEN sqlc.narg('parent_id')::uuid IS NOT NULL THEN sqlc.narg('parent_id')::uuid
        WHEN sqlc.narg('clear_parent')::boolean = true THEN NULL
        ELSE parent_id
    END,
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteFolder :exec
DELETE FROM folders WHERE id = $1;

-- name: GetFolderDescendants :many
-- Returns the folder and all its descendants (via dot_path prefix match)
SELECT f.* FROM folders f
WHERE f.dot_path = (SELECT f2.dot_path FROM folders f2 WHERE f2.id = $1)
   OR f.dot_path LIKE (SELECT f2.dot_path FROM folders f2 WHERE f2.id = $1) || '.%'
ORDER BY f.dot_path;

-- name: CountFolderDescendants :one
SELECT COUNT(*) FROM folders f
WHERE f.dot_path = (SELECT f2.dot_path FROM folders f2 WHERE f2.id = $1)
   OR f.dot_path LIKE (SELECT f2.dot_path FROM folders f2 WHERE f2.id = $1) || '.%';

-- name: UpdateFolderDotPath :exec
-- Update dot_path for a folder and all its descendants when moved
UPDATE folders SET
    dot_path = sqlc.arg('new_prefix')::text || SUBSTRING(dot_path FROM sqlc.arg('old_prefix_len')::int + 1),
    updated_at = NOW()
WHERE dot_path = sqlc.arg('old_prefix') OR dot_path LIKE sqlc.arg('old_prefix') || '.%';

-- name: GetNextFolderOrder :one
SELECT COALESCE(MAX("order"), 0) + 1 FROM folders WHERE org_id = $1 AND parent_id IS NOT DISTINCT FROM $2;

-- name: ReorderFolders :exec
-- Update the order of folders for drag-and-drop reordering
UPDATE folders SET "order" = $2, updated_at = NOW() WHERE id = $1;

-- name: ListChildFolders :many
SELECT * FROM folders WHERE parent_id = $1 ORDER BY "order", name;

-- name: ListRootFolders :many
SELECT * FROM folders WHERE org_id = $1 AND parent_id IS NULL ORDER BY "order", name LIMIT $2 OFFSET $3;
