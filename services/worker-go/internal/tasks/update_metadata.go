package tasks

import (
	"encoding/json"

	"github.com/hibiken/asynq"
)

// NewUpdateMetadataTask creates a new metadata update task.
func NewUpdateMetadataTask(payload UpdateMetadataPayload) (*asynq.Task, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	return asynq.NewTask(TypeUpdateMetadata, data), nil
}

// ParseUpdateMetadataPayload parses the payload from an update metadata task.
func ParseUpdateMetadataPayload(task *asynq.Task) (UpdateMetadataPayload, error) {
	var payload UpdateMetadataPayload
	if err := json.Unmarshal(task.Payload(), &payload); err != nil {
		return UpdateMetadataPayload{}, err
	}
	return payload, nil
}
