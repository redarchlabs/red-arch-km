package handlers

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestToMembershipResponse(t *testing.T) {
	now := time.Now().UTC()
	membershipID := uuid.New()
	profileID := uuid.New()
	orgID := uuid.New()
	regionID := uuid.New()
	deptID := uuid.New()
	roleID := uuid.New()
	groupID := uuid.New()

	membership := repository.UserOrgMembership{
		ID:         pgtype.UUID{Bytes: membershipID, Valid: true},
		ProfileID:  pgtype.UUID{Bytes: profileID, Valid: true},
		OrgID:      pgtype.UUID{Bytes: orgID, Valid: true},
		IsOrgAdmin: pgtype.Bool{Bool: true, Valid: true},
		CreatedAt:  pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:  pgtype.Timestamptz{Time: now, Valid: true},
	}

	regions := []repository.Region{
		{
			ID:               pgtype.UUID{Bytes: regionID, Valid: true},
			Name:             "US East",
			PermissionNumber: pgtype.Int2{Int16: 1, Valid: true},
		},
	}

	departments := []repository.Department{
		{
			ID:               pgtype.UUID{Bytes: deptID, Valid: true},
			Name:             "Engineering",
			PermissionNumber: pgtype.Int2{Int16: 2, Valid: true},
		},
	}

	roles := []repository.Role{
		{
			ID:               pgtype.UUID{Bytes: roleID, Valid: true},
			Name:             "Developer",
			PermissionNumber: pgtype.Int2{Int16: 3, Valid: true},
		},
	}

	groups := []repository.Group{
		{
			ID:               pgtype.UUID{Bytes: groupID, Valid: true},
			Name:             "Team Alpha",
			PermissionNumber: pgtype.Int2{Int16: 4, Valid: true},
		},
	}

	resp := toMembershipResponse(membership, regions, departments, roles, groups)

	if resp.ID != membershipID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, membershipID.String())
	}
	if resp.ProfileID != profileID.String() {
		t.Errorf("ProfileID = %v, want %v", resp.ProfileID, profileID.String())
	}
	if resp.OrgID != orgID.String() {
		t.Errorf("OrgID = %v, want %v", resp.OrgID, orgID.String())
	}
	if !resp.IsOrgAdmin {
		t.Error("IsOrgAdmin should be true")
	}
	if len(resp.Regions) != 1 {
		t.Errorf("Regions length = %d, want 1", len(resp.Regions))
	}
	if resp.Regions[0].Name != "US East" {
		t.Errorf("Region name = %v, want US East", resp.Regions[0].Name)
	}
	if len(resp.Departments) != 1 {
		t.Errorf("Departments length = %d, want 1", len(resp.Departments))
	}
	if len(resp.Roles) != 1 {
		t.Errorf("Roles length = %d, want 1", len(resp.Roles))
	}
	if len(resp.Groups) != 1 {
		t.Errorf("Groups length = %d, want 1", len(resp.Groups))
	}
}

func TestToMembershipResponse_EmptyDimensions(t *testing.T) {
	now := time.Now().UTC()
	membershipID := uuid.New()
	profileID := uuid.New()
	orgID := uuid.New()

	membership := repository.UserOrgMembership{
		ID:         pgtype.UUID{Bytes: membershipID, Valid: true},
		ProfileID:  pgtype.UUID{Bytes: profileID, Valid: true},
		OrgID:      pgtype.UUID{Bytes: orgID, Valid: true},
		IsOrgAdmin: pgtype.Bool{Bool: false, Valid: true},
		CreatedAt:  pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:  pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toMembershipResponse(membership, nil, nil, nil, nil)

	if resp.IsOrgAdmin {
		t.Error("IsOrgAdmin should be false")
	}
	if len(resp.Regions) != 0 {
		t.Errorf("Regions length = %d, want 0", len(resp.Regions))
	}
	if len(resp.Departments) != 0 {
		t.Errorf("Departments length = %d, want 0", len(resp.Departments))
	}
	if len(resp.Roles) != 0 {
		t.Errorf("Roles length = %d, want 0", len(resp.Roles))
	}
	if len(resp.Groups) != 0 {
		t.Errorf("Groups length = %d, want 0", len(resp.Groups))
	}
}

func TestValidationError(t *testing.T) {
	err := &validationError{field: "region_ids", message: "invalid UUID"}

	expected := "region_ids: invalid UUID"
	if err.Error() != expected {
		t.Errorf("Error() = %v, want %v", err.Error(), expected)
	}
}

func TestHttpError(t *testing.T) {
	err := &httpError{code: 403, message: "forbidden"}

	if err.Error() != "forbidden" {
		t.Errorf("Error() = %v, want forbidden", err.Error())
	}
}

func TestErrNotOrgAdmin(t *testing.T) {
	if ErrNotOrgAdmin.Error() != "Org admin required" {
		t.Errorf("ErrNotOrgAdmin.Error() = %v, want 'Org admin required'", ErrNotOrgAdmin.Error())
	}
}
