// Package tasks defines task payloads and constants for the worker.
package tasks

// Task type constants for asynq.
const (
	TypeIngestDocument  = "document:ingest"
	TypeRemoveDocument  = "document:remove"
	TypeUpdateMetadata  = "document:update_metadata"
)

// IngestPayload is the payload for document ingestion tasks.
type IngestPayload struct {
	DocumentID        string         `json:"document_id"`
	TenantID          string         `json:"tenant_id"`
	DocumentKey       string         `json:"document_key"`
	Title             string         `json:"title"`
	Text              string         `json:"text"`
	Tags              []string       `json:"tags"`
	AccessKeys        []int          `json:"access_keys"`
	UseKnowledgeGraph bool           `json:"use_knowledge_graph"`
	Metadata          map[string]any `json:"metadata"`
}

// RemovePayload is the payload for document removal tasks.
type RemovePayload struct {
	TenantID    string `json:"tenant_id"`
	DocumentKey string `json:"document_key"`
}

// UpdateMetadataPayload is the payload for metadata update tasks.
type UpdateMetadataPayload struct {
	TenantID      string   `json:"tenant_id"`
	DocumentKey   string   `json:"document_key"`
	Title         *string  `json:"title,omitempty"`
	NewTags       []string `json:"new_tags,omitempty"`
	NewAccessKeys []int    `json:"new_access_keys,omitempty"`
}

// StatusUpdate is sent to the API to update document processing status.
type StatusUpdate struct {
	TenantID string         `json:"tenant_id"`
	Status   string         `json:"status"`
	Details  map[string]any `json:"details,omitempty"`
}

// Processing status constants.
const (
	StatusProcessing = "PROCESSING"
	StatusSuccess    = "SUCCESS"
	StatusFailed     = "FAILED"
)
