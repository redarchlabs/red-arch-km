package handlers

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestDerefString(t *testing.T) {
	tests := []struct {
		name  string
		input *string
		want  string
	}{
		{
			name:  "nil pointer",
			input: nil,
			want:  "",
		},
		{
			name:  "empty string",
			input: strPtr(""),
			want:  "",
		},
		{
			name:  "non-empty string",
			input: strPtr("hello"),
			want:  "hello",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := derefString(tt.input); got != tt.want {
				t.Errorf("derefString() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestDerefBool(t *testing.T) {
	tests := []struct {
		name  string
		input *bool
		want  bool
	}{
		{
			name:  "nil pointer",
			input: nil,
			want:  false,
		},
		{
			name:  "true",
			input: boolPtr(true),
			want:  true,
		},
		{
			name:  "false",
			input: boolPtr(false),
			want:  false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := derefBool(tt.input); got != tt.want {
				t.Errorf("derefBool() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestToOrgResponse(t *testing.T) {
	now := time.Now().UTC()
	orgID := uuid.New()
	description := "Test org description"

	org := repository.Org{
		ID:   pgtype.UUID{Bytes: orgID, Valid: true},
		Name: "Test Org",
		Description: pgtype.Text{
			String: description,
			Valid:  true,
		},
		UseKnowledgeGraph: pgtype.Bool{Bool: true, Valid: true},
		CreatedAt:         pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:         pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toOrgResponse(org)

	if resp.ID != orgID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, orgID.String())
	}
	if resp.Name != "Test Org" {
		t.Errorf("Name = %v, want %v", resp.Name, "Test Org")
	}
	if resp.Description == nil || *resp.Description != description {
		t.Errorf("Description = %v, want %v", resp.Description, description)
	}
	if !resp.UseKnowledgeGraph {
		t.Error("UseKnowledgeGraph should be true")
	}
}

func TestToOrgResponse_NilDescription(t *testing.T) {
	now := time.Now().UTC()
	orgID := uuid.New()

	org := repository.Org{
		ID:                pgtype.UUID{Bytes: orgID, Valid: true},
		Name:              "Test Org",
		Description:       pgtype.Text{Valid: false},
		UseKnowledgeGraph: pgtype.Bool{Bool: false, Valid: true},
		CreatedAt:         pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:         pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toOrgResponse(org)

	if resp.Description != nil {
		t.Errorf("Description should be nil, got %v", *resp.Description)
	}
}

// Helper functions
func strPtr(s string) *string {
	return &s
}

func boolPtr(b bool) *bool {
	return &b
}
