package tasks

import (
	"testing"

	"github.com/hibiken/asynq"
)

func TestNewIngestTask(t *testing.T) {
	payload := IngestPayload{
		DocumentID:        "doc-123",
		TenantID:          "tenant-456",
		DocumentKey:       "key-789",
		Title:             "Test Document",
		Text:              "This is test content",
		Tags:              []string{"tag1", "tag2"},
		AccessKeys:        []int{1, 2, 3},
		UseKnowledgeGraph: true,
		Metadata:          map[string]any{"key": "value"},
	}

	task, err := NewIngestTask(payload)
	if err != nil {
		t.Fatalf("failed to create task: %v", err)
	}

	if task.Type() != TypeIngestDocument {
		t.Errorf("expected type %s, got %s", TypeIngestDocument, task.Type())
	}

	// Parse back
	parsed, err := ParseIngestPayload(task)
	if err != nil {
		t.Fatalf("failed to parse payload: %v", err)
	}

	if parsed.DocumentID != payload.DocumentID {
		t.Errorf("document ID mismatch: %s != %s", parsed.DocumentID, payload.DocumentID)
	}
	if parsed.TenantID != payload.TenantID {
		t.Errorf("tenant ID mismatch: %s != %s", parsed.TenantID, payload.TenantID)
	}
	if parsed.DocumentKey != payload.DocumentKey {
		t.Errorf("document key mismatch: %s != %s", parsed.DocumentKey, payload.DocumentKey)
	}
	if parsed.Title != payload.Title {
		t.Errorf("title mismatch: %s != %s", parsed.Title, payload.Title)
	}
	if parsed.Text != payload.Text {
		t.Errorf("text mismatch: %s != %s", parsed.Text, payload.Text)
	}
	if len(parsed.Tags) != len(payload.Tags) {
		t.Errorf("tags length mismatch: %d != %d", len(parsed.Tags), len(payload.Tags))
	}
	if len(parsed.AccessKeys) != len(payload.AccessKeys) {
		t.Errorf("access keys length mismatch: %d != %d", len(parsed.AccessKeys), len(payload.AccessKeys))
	}
	if parsed.UseKnowledgeGraph != payload.UseKnowledgeGraph {
		t.Errorf("use knowledge graph mismatch")
	}
}

func TestNewRemoveTask(t *testing.T) {
	payload := RemovePayload{
		TenantID:    "tenant-456",
		DocumentKey: "key-789",
	}

	task, err := NewRemoveTask(payload)
	if err != nil {
		t.Fatalf("failed to create task: %v", err)
	}

	if task.Type() != TypeRemoveDocument {
		t.Errorf("expected type %s, got %s", TypeRemoveDocument, task.Type())
	}

	parsed, err := ParseRemovePayload(task)
	if err != nil {
		t.Fatalf("failed to parse payload: %v", err)
	}

	if parsed.TenantID != payload.TenantID {
		t.Errorf("tenant ID mismatch: %s != %s", parsed.TenantID, payload.TenantID)
	}
	if parsed.DocumentKey != payload.DocumentKey {
		t.Errorf("document key mismatch: %s != %s", parsed.DocumentKey, payload.DocumentKey)
	}
}

func TestNewUpdateMetadataTask(t *testing.T) {
	title := "New Title"
	payload := UpdateMetadataPayload{
		TenantID:      "tenant-456",
		DocumentKey:   "key-789",
		Title:         &title,
		NewTags:       []string{"newtag1", "newtag2"},
		NewAccessKeys: []int{4, 5, 6},
	}

	task, err := NewUpdateMetadataTask(payload)
	if err != nil {
		t.Fatalf("failed to create task: %v", err)
	}

	if task.Type() != TypeUpdateMetadata {
		t.Errorf("expected type %s, got %s", TypeUpdateMetadata, task.Type())
	}

	parsed, err := ParseUpdateMetadataPayload(task)
	if err != nil {
		t.Fatalf("failed to parse payload: %v", err)
	}

	if parsed.TenantID != payload.TenantID {
		t.Errorf("tenant ID mismatch: %s != %s", parsed.TenantID, payload.TenantID)
	}
	if parsed.DocumentKey != payload.DocumentKey {
		t.Errorf("document key mismatch: %s != %s", parsed.DocumentKey, payload.DocumentKey)
	}
	if parsed.Title == nil || *parsed.Title != title {
		t.Errorf("title mismatch")
	}
	if len(parsed.NewTags) != len(payload.NewTags) {
		t.Errorf("tags length mismatch: %d != %d", len(parsed.NewTags), len(payload.NewTags))
	}
	if len(parsed.NewAccessKeys) != len(payload.NewAccessKeys) {
		t.Errorf("access keys length mismatch: %d != %d", len(parsed.NewAccessKeys), len(payload.NewAccessKeys))
	}
}

func TestParseInvalidPayload(t *testing.T) {
	t.Run("ingest invalid payload", func(t *testing.T) {
		invalidTask := asynq.NewTask(TypeIngestDocument, []byte("invalid json"))
		_, err := ParseIngestPayload(invalidTask)
		if err == nil {
			t.Error("expected error for invalid payload")
		}
	})

	t.Run("remove invalid payload", func(t *testing.T) {
		invalidTask := asynq.NewTask(TypeRemoveDocument, []byte("invalid json"))
		_, err := ParseRemovePayload(invalidTask)
		if err == nil {
			t.Error("expected error for invalid payload")
		}
	})

	t.Run("metadata invalid payload", func(t *testing.T) {
		invalidTask := asynq.NewTask(TypeUpdateMetadata, []byte("invalid json"))
		_, err := ParseUpdateMetadataPayload(invalidTask)
		if err == nil {
			t.Error("expected error for invalid payload")
		}
	})
}
