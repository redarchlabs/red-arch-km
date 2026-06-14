package handlers

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestToRegionResponse(t *testing.T) {
	now := time.Now().UTC()
	regionID := uuid.New()
	orgID := uuid.New()
	description := "Test region description"

	region := repository.Region{
		ID:   pgtype.UUID{Bytes: regionID, Valid: true},
		Name: "Test Region",
		Description: pgtype.Text{
			String: description,
			Valid:  true,
		},
		PermissionNumber: pgtype.Int2{Int16: 5, Valid: true},
		OrgID:            pgtype.UUID{Bytes: orgID, Valid: true},
		CreatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toRegionResponse(region)

	if resp.ID != regionID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, regionID.String())
	}
	if resp.Name != "Test Region" {
		t.Errorf("Name = %v, want %v", resp.Name, "Test Region")
	}
	if resp.Description == nil || *resp.Description != description {
		t.Errorf("Description = %v, want %v", resp.Description, description)
	}
	if resp.PermissionNumber != 5 {
		t.Errorf("PermissionNumber = %v, want %v", resp.PermissionNumber, 5)
	}
	if resp.OrgID != orgID.String() {
		t.Errorf("OrgID = %v, want %v", resp.OrgID, orgID.String())
	}
}

func TestToDepartmentResponse(t *testing.T) {
	now := time.Now().UTC()
	deptID := uuid.New()
	orgID := uuid.New()

	dept := repository.Department{
		ID:               pgtype.UUID{Bytes: deptID, Valid: true},
		Name:             "Engineering",
		Description:      pgtype.Text{Valid: false},
		PermissionNumber: pgtype.Int2{Int16: 10, Valid: true},
		OrgID:            pgtype.UUID{Bytes: orgID, Valid: true},
		CreatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toDepartmentResponse(dept)

	if resp.ID != deptID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, deptID.String())
	}
	if resp.Name != "Engineering" {
		t.Errorf("Name = %v, want %v", resp.Name, "Engineering")
	}
	if resp.Description != nil {
		t.Errorf("Description should be nil, got %v", *resp.Description)
	}
	if resp.PermissionNumber != 10 {
		t.Errorf("PermissionNumber = %v, want %v", resp.PermissionNumber, 10)
	}
}

func TestToRoleResponse(t *testing.T) {
	now := time.Now().UTC()
	roleID := uuid.New()
	orgID := uuid.New()
	description := "Admin role"

	role := repository.Role{
		ID:   pgtype.UUID{Bytes: roleID, Valid: true},
		Name: "Admin",
		Description: pgtype.Text{
			String: description,
			Valid:  true,
		},
		PermissionNumber: pgtype.Int2{Int16: 1, Valid: true},
		OrgID:            pgtype.UUID{Bytes: orgID, Valid: true},
		CreatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toRoleResponse(role)

	if resp.ID != roleID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, roleID.String())
	}
	if resp.Name != "Admin" {
		t.Errorf("Name = %v, want %v", resp.Name, "Admin")
	}
	if resp.Description == nil || *resp.Description != description {
		t.Errorf("Description = %v, want %v", resp.Description, description)
	}
	if resp.PermissionNumber != 1 {
		t.Errorf("PermissionNumber = %v, want %v", resp.PermissionNumber, 1)
	}
}

func TestToGroupResponse(t *testing.T) {
	now := time.Now().UTC()
	groupID := uuid.New()
	orgID := uuid.New()

	group := repository.Group{
		ID:               pgtype.UUID{Bytes: groupID, Valid: true},
		Name:             "Team Alpha",
		Description:      pgtype.Text{Valid: false},
		PermissionNumber: pgtype.Int2{Int16: 15, Valid: true},
		OrgID:            pgtype.UUID{Bytes: orgID, Valid: true},
		CreatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toGroupResponse(group)

	if resp.ID != groupID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, groupID.String())
	}
	if resp.Name != "Team Alpha" {
		t.Errorf("Name = %v, want %v", resp.Name, "Team Alpha")
	}
	if resp.Description != nil {
		t.Errorf("Description should be nil, got %v", *resp.Description)
	}
	if resp.PermissionNumber != 15 {
		t.Errorf("PermissionNumber = %v, want %v", resp.PermissionNumber, 15)
	}
	if resp.OrgID != orgID.String() {
		t.Errorf("OrgID = %v, want %v", resp.OrgID, orgID.String())
	}
}
