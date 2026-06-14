// Package services provides business logic services for the API.
package services

import (
	"context"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/packages/accessmask"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// PermissionService handles permission-related calculations.
type PermissionService struct {
	queries *repository.Queries
}

// NewPermissionService creates a new PermissionService.
func NewPermissionService(queries *repository.Queries) *PermissionService {
	return &PermissionService{queries: queries}
}

// CalculateUserMasksFromMembership generates all access masks a user can assert
// via their membership. Generates the Cartesian product of regions × departments × roles × groups.
func CalculateUserMasksFromMembership(
	orgNumber int16,
	regions []repository.Region,
	departments []repository.Department,
	roles []repository.Role,
	groups []repository.Group,
) []int64 {
	// Use 0 if empty (no assignment means permission_number 0)
	regionNums := extractPermissionNumbers(regions, func(r repository.Region) int16 { return r.PermissionNumber.Int16 })
	if len(regionNums) == 0 {
		regionNums = []int16{0}
	}
	deptNums := extractPermissionNumbers(departments, func(d repository.Department) int16 { return d.PermissionNumber.Int16 })
	if len(deptNums) == 0 {
		deptNums = []int16{0}
	}
	roleNums := extractPermissionNumbers(roles, func(r repository.Role) int16 { return r.PermissionNumber.Int16 })
	if len(roleNums) == 0 {
		roleNums = []int16{0}
	}
	groupNums := extractPermissionNumbers(groups, func(g repository.Group) int16 { return g.PermissionNumber.Int16 })
	if len(groupNums) == 0 {
		groupNums = []int16{0}
	}

	// Cartesian product
	var masks []int64
	for _, region := range regionNums {
		for _, dept := range deptNums {
			for _, role := range roleNums {
				for _, group := range groupNums {
					mask, err := accessmask.Encode(
						uint16(orgNumber),
						uint8(region),
						uint8(role),
						uint8(group),
						uint8(dept),
					)
					if err == nil {
						masks = append(masks, int64(mask))
					}
				}
			}
		}
	}
	return masks
}

// extractPermissionNumbers extracts permission numbers from a slice using a getter function.
func extractPermissionNumbers[T any](items []T, getter func(T) int16) []int16 {
	result := make([]int16, len(items))
	for i, item := range items {
		result[i] = getter(item)
	}
	return result
}

// HasFolderAccess checks if user masks grant access to a folder's view masks.
func HasFolderAccess(userMasks []int64, folderViewMasks []int64) bool {
	if len(folderViewMasks) == 0 {
		// No masks = publicly visible to all org members
		return true
	}
	for _, userMask := range userMasks {
		for _, folderMask := range folderViewMasks {
			if accessmask.Matches(uint32(userMask), uint32(folderMask)) {
				return true
			}
		}
	}
	return false
}

// FilterVisibleFolders filters folders to only those visible to the user.
func FilterVisibleFolders(folders []repository.Folder, userMasks []int64) []repository.Folder {
	if userMasks == nil {
		// nil = org admin, sees all
		return folders
	}
	var visible []repository.Folder
	for _, folder := range folders {
		if HasFolderAccess(userMasks, folder.ViewPermissionMasks) {
			visible = append(visible, folder)
		}
	}
	return visible
}

// PermissionConfigEntry represents a single entry in a permission config.
type PermissionConfigEntry struct {
	Region     string `json:"region,omitempty"`
	Department string `json:"department,omitempty"`
	Role       string `json:"role,omitempty"`
	Group      string `json:"group,omitempty"`
}

// PermissionConfigToMasks resolves permission config entries to access masks.
// Each entry becomes one mask; unmatched dimensions use wildcard (MAX).
func (s *PermissionService) PermissionConfigToMasks(
	ctx context.Context,
	orgID uuid.UUID,
	config []PermissionConfigEntry,
) ([]int64, error) {
	if len(config) == 0 {
		return nil, nil
	}

	// Get org permission number
	org, err := s.queries.GetOrg(ctx, toPgUUID(orgID))
	if err != nil {
		return nil, err
	}
	orgNumber := uint16(org.PermissionNumber.Int16)

	var masks []int64
	for _, entry := range config {
		var region, dept, role, group uint8

		// Resolve each dimension, use MAX (wildcard) if not specified
		if entry.Region != "" {
			r, err := s.queries.GetRegionByName(ctx, repository.GetRegionByNameParams{
				Name:  entry.Region,
				OrgID: toPgUUID(orgID),
			})
			if err != nil {
				continue // Skip entries with unresolved dimensions
			}
			region = uint8(r.PermissionNumber.Int16)
		} else {
			region = accessmask.MaxRegion
		}

		if entry.Department != "" {
			d, err := s.queries.GetDepartmentByName(ctx, repository.GetDepartmentByNameParams{
				Name:  entry.Department,
				OrgID: toPgUUID(orgID),
			})
			if err != nil {
				continue
			}
			dept = uint8(d.PermissionNumber.Int16)
		} else {
			dept = accessmask.MaxDept
		}

		if entry.Role != "" {
			r, err := s.queries.GetRoleByName(ctx, repository.GetRoleByNameParams{
				Name:  entry.Role,
				OrgID: toPgUUID(orgID),
			})
			if err != nil {
				continue
			}
			role = uint8(r.PermissionNumber.Int16)
		} else {
			role = accessmask.MaxRole
		}

		if entry.Group != "" {
			g, err := s.queries.GetGroupByName(ctx, repository.GetGroupByNameParams{
				Name:  entry.Group,
				OrgID: toPgUUID(orgID),
			})
			if err != nil {
				continue
			}
			group = uint8(g.PermissionNumber.Int16)
		} else {
			group = accessmask.MaxGroup
		}

		mask, err := accessmask.Encode(orgNumber, region, role, group, dept)
		if err == nil {
			masks = append(masks, int64(mask))
		}
	}

	return masks, nil
}

// toPgUUID converts a uuid.UUID to pgtype.UUID.
func toPgUUID(id uuid.UUID) pgtype.UUID {
	return pgtype.UUID{Bytes: id, Valid: true}
}
