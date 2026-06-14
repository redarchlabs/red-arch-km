package tasks

import (
	"encoding/json"

	"github.com/hibiken/asynq"
)

// NewIngestTask creates a new document ingestion task.
func NewIngestTask(payload IngestPayload) (*asynq.Task, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	return asynq.NewTask(TypeIngestDocument, data), nil
}

// ParseIngestPayload parses the payload from an ingest task.
func ParseIngestPayload(task *asynq.Task) (IngestPayload, error) {
	var payload IngestPayload
	if err := json.Unmarshal(task.Payload(), &payload); err != nil {
		return IngestPayload{}, err
	}
	return payload, nil
}
