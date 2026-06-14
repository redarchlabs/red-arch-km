package handlers

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

func TestToDocumentResponse(t *testing.T) {
	now := time.Now().UTC()
	docID := uuid.New()
	folderID := uuid.New()

	doc := repository.Document{
		ID:               pgtype.UUID{Bytes: docID, Valid: true},
		DocumentKey:      "doc-key-123",
		Title:            "Test Document",
		Description:      pgtype.Text{String: "Test description", Valid: true},
		ProcessingStatus: pgtype.Text{String: "pending", Valid: true},
		FolderID:         pgtype.UUID{Bytes: folderID, Valid: true},
		CreatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
	}

	tags := []repository.Tag{
		{
			ID:   pgtype.UUID{Bytes: uuid.New(), Valid: true},
			Name: "important",
		},
		{
			ID:   pgtype.UUID{Bytes: uuid.New(), Valid: true},
			Name: "finance",
		},
	}

	resp := toDocumentResponse(doc, tags)

	if resp.ID != docID.String() {
		t.Errorf("ID = %v, want %v", resp.ID, docID.String())
	}
	if resp.DocumentKey != "doc-key-123" {
		t.Errorf("DocumentKey = %v, want doc-key-123", resp.DocumentKey)
	}
	if resp.Title != "Test Document" {
		t.Errorf("Title = %v, want Test Document", resp.Title)
	}
	if resp.Description == nil || *resp.Description != "Test description" {
		t.Errorf("Description = %v, want Test description", resp.Description)
	}
	if resp.ProcessingStatus != "pending" {
		t.Errorf("ProcessingStatus = %v, want pending", resp.ProcessingStatus)
	}
	if resp.FolderID == nil || *resp.FolderID != folderID.String() {
		t.Errorf("FolderID = %v, want %v", resp.FolderID, folderID.String())
	}
	if len(resp.Tags) != 2 {
		t.Errorf("Tags len = %d, want 2", len(resp.Tags))
	}
}

func TestToDocumentResponse_NilFields(t *testing.T) {
	now := time.Now().UTC()
	docID := uuid.New()

	doc := repository.Document{
		ID:               pgtype.UUID{Bytes: docID, Valid: true},
		DocumentKey:      "doc-key-456",
		Title:            "Minimal Document",
		Description:      pgtype.Text{Valid: false}, // nil
		ProcessingStatus: pgtype.Text{String: "ready", Valid: true},
		FolderID:         pgtype.UUID{Valid: false}, // nil (unfiled)
		CreatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
	}

	resp := toDocumentResponse(doc, nil)

	if resp.Description != nil {
		t.Errorf("Description should be nil, got %v", *resp.Description)
	}
	if resp.FolderID != nil {
		t.Errorf("FolderID should be nil, got %v", *resp.FolderID)
	}
	if len(resp.Tags) != 0 {
		t.Errorf("Tags should be empty, got %d tags", len(resp.Tags))
	}
}

func TestToDocumentResponse_EmptyTags(t *testing.T) {
	now := time.Now().UTC()
	docID := uuid.New()

	doc := repository.Document{
		ID:               pgtype.UUID{Bytes: docID, Valid: true},
		DocumentKey:      "doc-key-789",
		Title:            "No Tags Document",
		ProcessingStatus: pgtype.Text{String: "processing", Valid: true},
		CreatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
		UpdatedAt:        pgtype.Timestamptz{Time: now, Valid: true},
	}

	// Empty tags slice
	resp := toDocumentResponse(doc, []repository.Tag{})

	if len(resp.Tags) != 0 {
		t.Errorf("Tags should be empty, got %d tags", len(resp.Tags))
	}
}
