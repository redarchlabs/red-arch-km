-- name: GetMembership :one
SELECT * FROM user_org_memberships WHERE id = $1;

-- name: GetMembershipByUserAndOrg :one
SELECT * FROM user_org_memberships
WHERE profile_id = $1 AND org_id = $2;

-- name: ListMembershipsInOrg :many
SELECT * FROM user_org_memberships
WHERE org_id = $1
ORDER BY created_at DESC
LIMIT $2 OFFSET $3;

-- name: CountMembershipsInOrg :one
SELECT COUNT(*) FROM user_org_memberships WHERE org_id = $1;

-- name: CreateMembership :one
INSERT INTO user_org_memberships (id, profile_id, org_id, is_org_admin)
VALUES ($1, $2, $3, $4)
RETURNING *;

-- name: UpsertMembership :one
INSERT INTO user_org_memberships (id, profile_id, org_id, is_org_admin)
VALUES ($1, $2, $3, $4)
ON CONFLICT (profile_id, org_id) DO UPDATE SET
    is_org_admin = EXCLUDED.is_org_admin,
    updated_at = NOW()
RETURNING *;

-- name: UpdateMembership :one
UPDATE user_org_memberships SET
    is_org_admin = COALESCE(sqlc.narg('is_org_admin'), is_org_admin),
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: DeleteMembership :exec
DELETE FROM user_org_memberships WHERE id = $1;

-- Membership regions junction
-- name: ClearMembershipRegions :exec
DELETE FROM membership_regions WHERE membership_id = $1;

-- name: AddMembershipRegion :exec
INSERT INTO membership_regions (membership_id, region_id) VALUES ($1, $2)
ON CONFLICT DO NOTHING;

-- name: ListMembershipRegions :many
SELECT r.* FROM regions r
JOIN membership_regions mr ON mr.region_id = r.id
WHERE mr.membership_id = $1;

-- Membership departments junction
-- name: ClearMembershipDepartments :exec
DELETE FROM membership_departments WHERE membership_id = $1;

-- name: AddMembershipDepartment :exec
INSERT INTO membership_departments (membership_id, department_id) VALUES ($1, $2)
ON CONFLICT DO NOTHING;

-- name: ListMembershipDepartments :many
SELECT d.* FROM departments d
JOIN membership_departments md ON md.department_id = d.id
WHERE md.membership_id = $1;

-- Membership roles junction
-- name: ClearMembershipRoles :exec
DELETE FROM membership_roles WHERE membership_id = $1;

-- name: AddMembershipRole :exec
INSERT INTO membership_roles (membership_id, role_id) VALUES ($1, $2)
ON CONFLICT DO NOTHING;

-- name: ListMembershipRoles :many
SELECT r.* FROM roles r
JOIN membership_roles mr ON mr.role_id = r.id
WHERE mr.membership_id = $1;

-- Membership groups junction
-- name: ClearMembershipGroups :exec
DELETE FROM membership_groups WHERE membership_id = $1;

-- name: AddMembershipGroup :exec
INSERT INTO membership_groups (membership_id, group_id) VALUES ($1, $2)
ON CONFLICT DO NOTHING;

-- name: ListMembershipGroups :many
SELECT g.* FROM groups g
JOIN membership_groups mg ON mg.group_id = g.id
WHERE mg.membership_id = $1;
