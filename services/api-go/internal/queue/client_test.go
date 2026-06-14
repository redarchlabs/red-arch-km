package queue

import (
	"encoding/json"
	"testing"
)

func TestIngestPayload_JSON(t *testing.T) {
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

	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}

	var parsed IngestPayload
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}

	if parsed.DocumentID != payload.DocumentID {
		t.Errorf("DocumentID mismatch: %s != %s", parsed.DocumentID, payload.DocumentID)
	}
	if parsed.TenantID != payload.TenantID {
		t.Errorf("TenantID mismatch: %s != %s", parsed.TenantID, payload.TenantID)
	}
	if parsed.DocumentKey != payload.DocumentKey {
		t.Errorf("DocumentKey mismatch: %s != %s", parsed.DocumentKey, payload.DocumentKey)
	}
	if parsed.Title != payload.Title {
		t.Errorf("Title mismatch: %s != %s", parsed.Title, payload.Title)
	}
	if parsed.Text != payload.Text {
		t.Errorf("Text mismatch: %s != %s", parsed.Text, payload.Text)
	}
	if len(parsed.Tags) != len(payload.Tags) {
		t.Errorf("Tags length mismatch: %d != %d", len(parsed.Tags), len(payload.Tags))
	}
	if len(parsed.AccessKeys) != len(payload.AccessKeys) {
		t.Errorf("AccessKeys length mismatch: %d != %d", len(parsed.AccessKeys), len(payload.AccessKeys))
	}
	if parsed.UseKnowledgeGraph != payload.UseKnowledgeGraph {
		t.Errorf("UseKnowledgeGraph mismatch")
	}
}

func TestRemovePayload_JSON(t *testing.T) {
	payload := RemovePayload{
		TenantID:    "tenant-456",
		DocumentKey: "key-789",
	}

	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}

	var parsed RemovePayload
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}

	if parsed.TenantID != payload.TenantID {
		t.Errorf("TenantID mismatch: %s != %s", parsed.TenantID, payload.TenantID)
	}
	if parsed.DocumentKey != payload.DocumentKey {
		t.Errorf("DocumentKey mismatch: %s != %s", parsed.DocumentKey, payload.DocumentKey)
	}
}

func TestUpdateMetadataPayload_JSON(t *testing.T) {
	title := "New Title"
	payload := UpdateMetadataPayload{
		TenantID:      "tenant-456",
		DocumentKey:   "key-789",
		Title:         &title,
		NewTags:       []string{"newtag1", "newtag2"},
		NewAccessKeys: []int{4, 5, 6},
	}

	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}

	var parsed UpdateMetadataPayload
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}

	if parsed.TenantID != payload.TenantID {
		t.Errorf("TenantID mismatch: %s != %s", parsed.TenantID, payload.TenantID)
	}
	if parsed.DocumentKey != payload.DocumentKey {
		t.Errorf("DocumentKey mismatch: %s != %s", parsed.DocumentKey, payload.DocumentKey)
	}
	if parsed.Title == nil || *parsed.Title != title {
		t.Errorf("Title mismatch")
	}
	if len(parsed.NewTags) != len(payload.NewTags) {
		t.Errorf("NewTags length mismatch: %d != %d", len(parsed.NewTags), len(payload.NewTags))
	}
	if len(parsed.NewAccessKeys) != len(payload.NewAccessKeys) {
		t.Errorf("NewAccessKeys length mismatch: %d != %d", len(parsed.NewAccessKeys), len(payload.NewAccessKeys))
	}
}

func TestUpdateMetadataPayload_OmitEmpty(t *testing.T) {
	// Test that nil/empty fields are omitted in JSON
	payload := UpdateMetadataPayload{
		TenantID:    "tenant-456",
		DocumentKey: "key-789",
		// Title, NewTags, NewAccessKeys are nil/empty
	}

	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}

	// Check that optional fields are not in JSON
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("failed to unmarshal to map: %v", err)
	}

	if _, exists := raw["title"]; exists {
		t.Error("expected title to be omitted")
	}
	if _, exists := raw["new_tags"]; exists {
		t.Error("expected new_tags to be omitted")
	}
	if _, exists := raw["new_access_keys"]; exists {
		t.Error("expected new_access_keys to be omitted")
	}
}

func TestTaskTypeConstants(t *testing.T) {
	// Ensure task type constants match expected values
	if TypeIngestDocument != "document:ingest" {
		t.Errorf("TypeIngestDocument mismatch: %s", TypeIngestDocument)
	}
	if TypeRemoveDocument != "document:remove" {
		t.Errorf("TypeRemoveDocument mismatch: %s", TypeRemoveDocument)
	}
	if TypeUpdateMetadata != "document:update_metadata" {
		t.Errorf("TypeUpdateMetadata mismatch: %s", TypeUpdateMetadata)
	}
}
