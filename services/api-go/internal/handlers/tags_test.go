package handlers

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestToTagResponse(t *testing.T) {
	now := time.Now().UTC()
	tagID := uuid.New()

	tag := repository.Tag{
		ID:        pgtype.UUID{Bytes: tagID, Valid: true},
		Name:      "important",
		CreatedAt: pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt: pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toTagResponse(tag)

	if resp.ID != tagID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, tagID.String())
	}
	if resp.Name != "important" {
		t.Errorf("Name = %v, want important", resp.Name)
	}
	if resp.CreatedAt == "" {
		t.Error("CreatedAt should not be empty")
	}
	if resp.UpdatedAt == "" {
		t.Error("UpdatedAt should not be empty")
	}
}

func TestToTagResponse_TimeFormat(t *testing.T) {
	specificTime := time.Date(2024, 6, 15, 10, 30, 45, 0, time.UTC)
	tagID := uuid.New()

	tag := repository.Tag{
		ID:        pgtype.UUID{Bytes: tagID, Valid: true},
		Name:      "test-tag",
		CreatedAt: pgtype.Timestamptz{Time: specificTime, Valid: true},
		UpdatedAt: pgtype.Timestamptz{Time: specificTime, Valid: true},
	}

	resp := toTagResponse(tag)

	expectedTime := "2024-06-15T10:30:45Z"
	if resp.CreatedAt != expectedTime {
		t.Errorf("CreatedAt = %v, want %v", resp.CreatedAt, expectedTime)
	}
	if resp.UpdatedAt != expectedTime {
		t.Errorf("UpdatedAt = %v, want %v", resp.UpdatedAt, expectedTime)
	}
}
