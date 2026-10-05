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
-- Returns the folder and all its descendants, walked by parent_id. Never by
-- dot_path: two root folders may share a name, and so a path prefix.
WITH RECURSIVE subtree AS (
    SELECT f0.id FROM folders f0 WHERE f0.id = $1
    UNION
    SELECT c.id FROM folders c JOIN subtree s ON c.parent_id = s.id
)
SELECT f.* FROM folders f
WHERE f.id IN (SELECT id FROM subtree)
ORDER BY f.dot_path;

-- name: CountFolderDescendants :one
-- The folder plus all its descendants, walked by parent_id (see GetFolderDescendants).
WITH RECURSIVE subtree AS (
    SELECT f0.id FROM folders f0 WHERE f0.id = $1
    UNION
    SELECT c.id FROM folders c JOIN subtree s ON c.parent_id = s.id
)
SELECT COUNT(*) FROM subtree;

-- name: UpdateFolderDotPath :exec
-- Set a folder's dot_path to new_prefix and rebuild every descendant's from its
-- names, walked by parent_id (a LIKE on the old path would also rewrite a
-- same-named root's subtree). The depth bound guards a corrupt cycle.
WITH RECURSIVE subtree(id, path, depth) AS (
    SELECT f0.id, sqlc.arg('new_prefix')::text, 0 FROM folders f0 WHERE f0.id = sqlc.arg('folder_id')
    UNION ALL
    SELECT c.id, s.path || '.' || c.name, s.depth + 1
    FROM folders c JOIN subtree s ON c.parent_id = s.id
    WHERE s.depth < 1000
)
UPDATE folders SET
    dot_path = subtree.path,
    updated_at = NOW()
FROM subtree
WHERE folders.id = subtree.id;

-- name: GetNextFolderOrder :one
SELECT COALESCE(MAX("order"), 0) + 1 FROM folders WHERE org_id = $1 AND parent_id IS NOT DISTINCT FROM $2;

-- name: ReorderFolders :exec
-- Update the order of folders for drag-and-drop reordering
UPDATE folders SET "order" = $2, updated_at = NOW() WHERE id = $1;

-- name: ListChildFolders :many
SELECT * FROM folders WHERE parent_id = $1 ORDER BY "order", name;

-- name: ListRootFolders :many
SELECT * FROM folders WHERE org_id = $1 AND parent_id IS NULL ORDER BY "order", name LIMIT $2 OFFSET $3;
