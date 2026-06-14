package handlers

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestToUserResponse(t *testing.T) {
	now := time.Now().UTC()
	userID := uuid.New()
	description := "Test user description"

	profile := repository.UserProfile{
		ID:       pgtype.UUID{Bytes: userID, Valid: true},
		Username: "testuser",
		Email:    "test@example.com",
		Description: pgtype.Text{
			String: description,
			Valid:  true,
		},
		IsSiteAdmin: pgtype.Bool{Bool: true, Valid: true},
		CreatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toUserResponse(profile)

	if resp.ID != userID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, userID.String())
	}
	if resp.Username != "testuser" {
		t.Errorf("Username = %v, want %v", resp.Username, "testuser")
	}
	if resp.Email != "test@example.com" {
		t.Errorf("Email = %v, want %v", resp.Email, "test@example.com")
	}
	if resp.Description == nil || *resp.Description != description {
		t.Errorf("Description = %v, want %v", resp.Description, description)
	}
	if !resp.IsSiteAdmin {
		t.Error("IsSiteAdmin should be true")
	}
}

func TestToUserResponse_NilDescription(t *testing.T) {
	now := time.Now().UTC()
	userID := uuid.New()

	profile := repository.UserProfile{
		ID:          pgtype.UUID{Bytes: userID, Valid: true},
		Username:    "testuser",
		Email:       "test@example.com",
		Description: pgtype.Text{Valid: false},
		IsSiteAdmin: pgtype.Bool{Bool: false, Valid: true},
		CreatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:   pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toUserResponse(profile)

	if resp.Description != nil {
		t.Errorf("Description should be nil, got %v", *resp.Description)
	}
	if resp.IsSiteAdmin {
		t.Error("IsSiteAdmin should be false")
	}
}
