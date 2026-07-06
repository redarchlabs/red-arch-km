-- Regions
-- name: GetRegion :one
SELECT * FROM regions WHERE id = $1;

-- name: ListRegions :many
SELECT * FROM regions ORDER BY name LIMIT $1 OFFSET $2;

-- name: ListRegionsForOrg :many
SELECT * FROM regions WHERE org_id = $1 ORDER BY name LIMIT $2 OFFSET $3;

-- name: CountRegions :one
SELECT COUNT(*) FROM regions;

-- name: CountRegionsForOrg :one
SELECT COUNT(*) FROM regions WHERE org_id = $1;

-- name: GetNextRegionPermissionNumber :one
SELECT COALESCE(MAX(permission_number), 0) + 1 FROM regions FOR UPDATE;

-- name: CreateRegion :one
INSERT INTO regions (id, name, description, permission_number, org_id)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: UpdateRegion :one
UPDATE regions SET
    name = COALESCE(sqlc.narg('name'), name),
    description = COALESCE(sqlc.narg('description'), description),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteRegion :exec
DELETE FROM regions WHERE id = $1;

-- name: GetRegionByName :one
SELECT * FROM regions WHERE name = $1 AND org_id = $2;

-- Departments
-- name: GetDepartment :one
SELECT * FROM departments WHERE id = $1;

-- name: ListDepartments :many
SELECT * FROM departments ORDER BY name LIMIT $1 OFFSET $2;

-- name: ListDepartmentsForOrg :many
SELECT * FROM departments WHERE org_id = $1 ORDER BY name LIMIT $2 OFFSET $3;

-- name: CountDepartments :one
SELECT COUNT(*) FROM departments;

-- name: CountDepartmentsForOrg :one
SELECT COUNT(*) FROM departments WHERE org_id = $1;

-- name: GetNextDepartmentPermissionNumber :one
SELECT COALESCE(MAX(permission_number), 0) + 1 FROM departments FOR UPDATE;

-- name: CreateDepartment :one
INSERT INTO departments (id, name, description, permission_number, org_id)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: UpdateDepartment :one
UPDATE departments SET
    name = COALESCE(sqlc.narg('name'), name),
    description = COALESCE(sqlc.narg('description'), description),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteDepartment :exec
DELETE FROM departments WHERE id = $1;

-- name: GetDepartmentByName :one
SELECT * FROM departments WHERE name = $1 AND org_id = $2;

-- Roles
-- name: GetRole :one
SELECT * FROM roles WHERE id = $1;

-- name: ListRoles :many
SELECT * FROM roles ORDER BY name LIMIT $1 OFFSET $2;

-- name: ListRolesForOrg :many
SELECT * FROM roles WHERE org_id = $1 ORDER BY name LIMIT $2 OFFSET $3;

-- name: CountRoles :one
SELECT COUNT(*) FROM roles;

-- name: CountRolesForOrg :one
SELECT COUNT(*) FROM roles WHERE org_id = $1;

-- name: GetNextRolePermissionNumber :one
SELECT COALESCE(MAX(permission_number), 0) + 1 FROM roles FOR UPDATE;

-- name: CreateRole :one
INSERT INTO roles (id, name, description, permission_number, org_id)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: UpdateRole :one
UPDATE roles SET
    name = COALESCE(sqlc.narg('name'), name),
    description = COALESCE(sqlc.narg('description'), description),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteRole :exec
DELETE FROM roles WHERE id = $1;

-- name: GetRoleByName :one
SELECT * FROM roles WHERE name = $1 AND org_id = $2;

-- Groups
-- name: GetGroup :one
SELECT * FROM groups WHERE id = $1;

-- name: ListGroups :many
SELECT * FROM groups ORDER BY name LIMIT $1 OFFSET $2;

-- name: ListGroupsForOrg :many
SELECT * FROM groups WHERE org_id = $1 ORDER BY name LIMIT $2 OFFSET $3;

-- name: CountGroups :one
SELECT COUNT(*) FROM groups;

-- name: CountGroupsForOrg :one
SELECT COUNT(*) FROM groups WHERE org_id = $1;

-- name: GetNextGroupPermissionNumber :one
SELECT COALESCE(MAX(permission_number), 0) + 1 FROM groups FOR UPDATE;

-- name: CreateGroup :one
INSERT INTO groups (id, name, description, permission_number, org_id)
VALUES ($1, $2, $3, $4, $5)
RETURNING *;

-- name: UpdateGroup :one
UPDATE groups SET
    name = COALESCE(sqlc.narg('name'), name),
    description = COALESCE(sqlc.narg('description'), description),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteGroup :exec
DELETE FROM groups WHERE id = $1;

-- name: GetGroupByName :one
SELECT * FROM groups WHERE name = $1 AND org_id = $2;
