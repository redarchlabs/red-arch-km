package handlers

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestToFolderResponse(t *testing.T) {
	now := time.Now().UTC()
	folderID := uuid.New()
	parentID := uuid.New()
	description := "Test folder description"

	folder := repository.Folder{
		ID:          pgtype.UUID{Bytes: folderID, Valid: true},
		Name:        "Test Folder",
		Description: pgtype.Text{String: description, Valid: true},
		Order:       pgtype.Int4{Int32: 5, Valid: true},
		DotPath:     pgtype.Text{String: "folder1.folder2", Valid: true},
		ParentID:    pgtype.UUID{Bytes: parentID, Valid: true},
		ViewPermissionMasks:        []int64{1234, 5678},
		ContributorPermissionMasks: []int64{9999},
		CreatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toFolderResponse(folder)

	if resp.ID != folderID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, folderID.String())
	}
	if resp.Name != "Test Folder" {
		t.Errorf("Name = %v, want %v", resp.Name, "Test Folder")
	}
	if resp.Description == nil || *resp.Description != description {
		t.Errorf("Description = %v, want %v", resp.Description, description)
	}
	if resp.Order != 5 {
		t.Errorf("Order = %v, want %v", resp.Order, 5)
	}
	if resp.DotPath != "folder1.folder2" {
		t.Errorf("DotPath = %v, want %v", resp.DotPath, "folder1.folder2")
	}
	if resp.ParentID == nil || *resp.ParentID != parentID.String() {
		t.Errorf("ParentID = %v, want %v", resp.ParentID, parentID.String())
	}
	if len(resp.ViewPermissionMasks) != 2 {
		t.Errorf("ViewPermissionMasks len = %v, want 2", len(resp.ViewPermissionMasks))
	}
	if len(resp.ContributorPermissionMasks) != 1 {
		t.Errorf("ContributorPermissionMasks len = %v, want 1", len(resp.ContributorPermissionMasks))
	}
}

func TestToFolderResponse_NilFields(t *testing.T) {
	now := time.Now().UTC()
	folderID := uuid.New()

	folder := repository.Folder{
		ID:          pgtype.UUID{Bytes: folderID, Valid: true},
		Name:        "Root Folder",
		Description: pgtype.Text{Valid: false}, // nil
		Order:       pgtype.Int4{Int32: 0, Valid: true},
		DotPath:     pgtype.Text{Valid: false}, // nil
		ParentID:    pgtype.UUID{Valid: false}, // nil (root folder)
		CreatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toFolderResponse(folder)

	if resp.Description != nil {
		t.Errorf("Description should be nil, got %v", *resp.Description)
	}
	if resp.DotPath != "" {
		t.Errorf("DotPath should be empty, got %v", resp.DotPath)
	}
	if resp.ParentID != nil {
		t.Errorf("ParentID should be nil, got %v", *resp.ParentID)
	}
}

func TestParsePermissionConfig(t *testing.T) {
	tests := []struct {
		name   string
		config []map[string]interface{}
		want   int
	}{
		{
			name:   "nil config",
			config: nil,
			want:   0,
		},
		{
			name:   "empty config",
			config: []map[string]interface{}{},
			want:   0,
		},
		{
			name: "single entry with region",
			config: []map[string]interface{}{
				{"region": "APAC"},
			},
			want: 1,
		},
		{
			name: "multiple entries",
			config: []map[string]interface{}{
				{"region": "APAC", "department": "Engineering"},
				{"role": "Manager"},
			},
			want: 2,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parsePermissionConfig(tt.config)
			if len(got) != tt.want {
				t.Errorf("parsePermissionConfig() len = %d, want %d", len(got), tt.want)
			}
		})
	}
}

func TestParsePermissionConfigFields(t *testing.T) {
	config := []map[string]interface{}{
		{
			"region":     "APAC",
			"department": "Engineering",
			"role":       "Manager",
			"group":      "Team A",
		},
	}

	got := parsePermissionConfig(config)

	if len(got) != 1 {
		t.Fatalf("expected 1 entry, got %d", len(got))
	}

	entry := got[0]
	if entry.Region != "APAC" {
		t.Errorf("Region = %v, want APAC", entry.Region)
	}
	if entry.Department != "Engineering" {
		t.Errorf("Department = %v, want Engineering", entry.Department)
	}
	if entry.Role != "Manager" {
		t.Errorf("Role = %v, want Manager", entry.Role)
	}
	if entry.Group != "Team A" {
		t.Errorf("Group = %v, want Team A", entry.Group)
	}
}
