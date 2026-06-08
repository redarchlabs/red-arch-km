package tasks

import (
	"encoding/json"

	"github.com/hibiken/asynq"
)

// NewRemoveTask creates a new document removal task.
func NewRemoveTask(payload RemovePayload) (*asynq.Task, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	return asynq.NewTask(TypeRemoveDocument, data), nil
}

// ParseRemovePayload parses the payload from a remove task.
func ParseRemovePayload(task *asynq.Task) (RemovePayload, error) {
	var payload RemovePayload
	if err := json.Unmarshal(task.Payload(), &payload); err != nil {
		return RemovePayload{}, err
	}
	return payload, nil
}
