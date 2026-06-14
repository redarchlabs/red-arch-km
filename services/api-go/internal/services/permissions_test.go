package services

import (
	"testing"

	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestCalculateUserMasksFromMembership(t *testing.T) {
	tests := []struct {
		name        string
		orgNumber   int16
		regions     []repository.Region
		departments []repository.Department
		roles       []repository.Role
		groups      []repository.Group
		wantLen     int
	}{
		{
			name:      "empty dimensions - returns single mask with all zeros",
			orgNumber: 1,
			wantLen:   1,
		},
		{
			name:      "single region",
			orgNumber: 1,
			regions: []repository.Region{
				{PermissionNumber: pgtype.Int2{Int16: 1, Valid: true}},
			},
			wantLen: 1, // 1 region x 1 (empty dept) x 1 (empty role) x 1 (empty group)
		},
		{
			name:      "cartesian product - 2 regions x 2 departments",
			orgNumber: 1,
			regions: []repository.Region{
				{PermissionNumber: pgtype.Int2{Int16: 1, Valid: true}},
				{PermissionNumber: pgtype.Int2{Int16: 2, Valid: true}},
			},
			departments: []repository.Department{
				{PermissionNumber: pgtype.Int2{Int16: 1, Valid: true}},
				{PermissionNumber: pgtype.Int2{Int16: 2, Valid: true}},
			},
			wantLen: 4, // 2 x 2 x 1 x 1
		},
		{
			name:      "full cartesian product",
			orgNumber: 1,
			regions: []repository.Region{
				{PermissionNumber: pgtype.Int2{Int16: 1, Valid: true}},
				{PermissionNumber: pgtype.Int2{Int16: 2, Valid: true}},
			},
			departments: []repository.Department{
				{PermissionNumber: pgtype.Int2{Int16: 1, Valid: true}},
			},
			roles: []repository.Role{
				{PermissionNumber: pgtype.Int2{Int16: 1, Valid: true}},
				{PermissionNumber: pgtype.Int2{Int16: 2, Valid: true}},
			},
			groups: []repository.Group{
				{PermissionNumber: pgtype.Int2{Int16: 1, Valid: true}},
			},
			wantLen: 4, // 2 x 1 x 2 x 1
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := CalculateUserMasksFromMembership(
				tt.orgNumber,
				tt.regions,
				tt.departments,
				tt.roles,
				tt.groups,
			)
			if len(got) != tt.wantLen {
				t.Errorf("CalculateUserMasksFromMembership() len = %d, want %d", len(got), tt.wantLen)
			}
		})
	}
}

func TestHasFolderAccess(t *testing.T) {
	tests := []struct {
		name            string
		userMasks       []int64
		folderViewMasks []int64
		want            bool
	}{
		{
			name:            "empty folder masks - public access",
			userMasks:       []int64{1234},
			folderViewMasks: []int64{},
			want:            true,
		},
		{
			name:            "nil folder masks - public access",
			userMasks:       []int64{1234},
			folderViewMasks: nil,
			want:            true,
		},
		{
			name:            "exact match",
			userMasks:       []int64{1234},
			folderViewMasks: []int64{1234},
			want:            true,
		},
		{
			name:            "no matching masks",
			userMasks:       []int64{1111},
			folderViewMasks: []int64{2222},
			want:            false,
		},
		{
			name:            "one matching mask among several",
			userMasks:       []int64{1111, 1234, 3333},
			folderViewMasks: []int64{5555, 1234, 7777},
			want:            true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := HasFolderAccess(tt.userMasks, tt.folderViewMasks)
			if got != tt.want {
				t.Errorf("HasFolderAccess() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestFilterVisibleFolders(t *testing.T) {
	folder1 := repository.Folder{
		Name:                "Public Folder",
		ViewPermissionMasks: nil, // Public
	}
	folder2 := repository.Folder{
		Name:                "Restricted Folder",
		ViewPermissionMasks: []int64{1234},
	}
	folder3 := repository.Folder{
		Name:                "Other Restricted Folder",
		ViewPermissionMasks: []int64{5678},
	}

	tests := []struct {
		name      string
		folders   []repository.Folder
		userMasks []int64
		wantLen   int
	}{
		{
			name:      "nil userMasks - org admin sees all",
			folders:   []repository.Folder{folder1, folder2, folder3},
			userMasks: nil,
			wantLen:   3,
		},
		{
			name:      "user sees public only",
			folders:   []repository.Folder{folder1, folder2, folder3},
			userMasks: []int64{9999}, // No match
			wantLen:   1,             // Only public folder
		},
		{
			name:      "user sees public and matching restricted",
			folders:   []repository.Folder{folder1, folder2, folder3},
			userMasks: []int64{1234},
			wantLen:   2, // Public + folder2
		},
		{
			name:      "empty folders",
			folders:   []repository.Folder{},
			userMasks: []int64{1234},
			wantLen:   0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := FilterVisibleFolders(tt.folders, tt.userMasks)
			if len(got) != tt.wantLen {
				t.Errorf("FilterVisibleFolders() len = %d, want %d", len(got), tt.wantLen)
			}
		})
	}
}

func TestExtractPermissionNumbers(t *testing.T) {
	regions := []repository.Region{
		{PermissionNumber: pgtype.Int2{Int16: 5, Valid: true}},
		{PermissionNumber: pgtype.Int2{Int16: 10, Valid: true}},
		{PermissionNumber: pgtype.Int2{Int16: 15, Valid: true}},
	}

	result := extractPermissionNumbers(regions, func(r repository.Region) int16 {
		return r.PermissionNumber.Int16
	})

	if len(result) != 3 {
		t.Fatalf("expected 3 results, got %d", len(result))
	}

	expected := []int16{5, 10, 15}
	for i, want := range expected {
		if result[i] != want {
			t.Errorf("result[%d] = %d, want %d", i, result[i], want)
		}
	}
}
