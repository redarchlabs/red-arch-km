// Package queue provides task queue client for enqueuing background tasks.
package queue

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/hibiken/asynq"
)

// Task type constants (must match worker-go/internal/tasks/types.go).
const (
	TypeIngestDocument = "document:ingest"
	TypeRemoveDocument = "document:remove"
	TypeUpdateMetadata = "document:update_metadata"
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

// Client provides methods for enqueueing background tasks.
type Client interface {
	EnqueueIngest(payload IngestPayload) (string, error)
	EnqueueRemove(payload RemovePayload) (string, error)
	EnqueueUpdateMetadata(payload UpdateMetadataPayload) (string, error)
	Close() error
}

// client implements Client using asynq.
type client struct {
	asynqClient *asynq.Client
	maxRetries  int
}

// NewClient creates a new queue client.
func NewClient(redisURL string, maxRetries int) (Client, error) {
	opt, err := asynq.ParseRedisURI(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}

	return &client{
		asynqClient: asynq.NewClient(opt),
		maxRetries:  maxRetries,
	}, nil
}

// Close closes the client connection.
func (c *client) Close() error {
	return c.asynqClient.Close()
}

// EnqueueIngest enqueues a document ingestion task.
func (c *client) EnqueueIngest(payload IngestPayload) (string, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshal payload: %w", err)
	}

	task := asynq.NewTask(TypeIngestDocument, data)
	info, err := c.asynqClient.Enqueue(task,
		asynq.MaxRetry(c.maxRetries),
		asynq.Timeout(5*time.Minute),
		asynq.Queue("default"),
	)
	if err != nil {
		return "", fmt.Errorf("enqueue task: %w", err)
	}

	return info.ID, nil
}

// EnqueueRemove enqueues a document removal task.
func (c *client) EnqueueRemove(payload RemovePayload) (string, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshal payload: %w", err)
	}

	task := asynq.NewTask(TypeRemoveDocument, data)
	info, err := c.asynqClient.Enqueue(task,
		asynq.MaxRetry(c.maxRetries),
		asynq.Timeout(1*time.Minute),
		asynq.Queue("default"),
	)
	if err != nil {
		return "", fmt.Errorf("enqueue task: %w", err)
	}

	return info.ID, nil
}

// EnqueueUpdateMetadata enqueues a metadata update task.
func (c *client) EnqueueUpdateMetadata(payload UpdateMetadataPayload) (string, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshal payload: %w", err)
	}

	task := asynq.NewTask(TypeUpdateMetadata, data)
	info, err := c.asynqClient.Enqueue(task,
		asynq.MaxRetry(c.maxRetries),
		asynq.Timeout(1*time.Minute),
		asynq.Queue("default"),
	)
	if err != nil {
		return "", fmt.Errorf("enqueue task: %w", err)
	}

	return info.ID, nil
}
