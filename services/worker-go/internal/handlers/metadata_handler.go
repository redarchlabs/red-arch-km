package handlers

import (
	"context"
	"errors"
	"log/slog"

	"github.com/hibiken/asynq"

	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/client"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/tasks"
)

// MetadataHandler handles metadata update tasks.
type MetadataHandler struct {
	brainClient client.BrainClient
}

// NewMetadataHandler creates a new metadata handler.
func NewMetadataHandler(brainClient client.BrainClient) *MetadataHandler {
	return &MetadataHandler{
		brainClient: brainClient,
	}
}

// ProcessTask processes a metadata update task.
func (h *MetadataHandler) ProcessTask(ctx context.Context, task *asynq.Task) error {
	payload, err := tasks.ParseUpdateMetadataPayload(task)
	if err != nil {
		slog.Error("failed to parse metadata update payload", "error", err)
		return nil // Don't retry malformed payloads
	}

	slog.Info("processing metadata update task",
		"document_key", payload.DocumentKey,
		"tenant_id", payload.TenantID,
	)

	if err := h.brainClient.UpdateMetadata(ctx, client.UpdateMetadataRequest{
		TenantID:      payload.TenantID,
		DocumentKey:   payload.DocumentKey,
		Title:         payload.Title,
		NewTags:       payload.NewTags,
		NewAccessKeys: payload.NewAccessKeys,
	}); err != nil {
		var httpErr *client.HTTPError
		if errors.As(err, &httpErr) {
			if httpErr.IsRetryable() {
				slog.Warn("transient brain-api error on metadata update, will retry",
					"document_key", payload.DocumentKey,
					"status_code", httpErr.StatusCode,
				)
				return err // Retry
			}
			slog.Error("permanent brain-api error updating metadata",
				"document_key", payload.DocumentKey,
				"status_code", httpErr.StatusCode,
			)
			return nil // Don't retry permanent errors
		}
		// Network error — retry
		slog.Warn("network error updating metadata, will retry",
			"document_key", payload.DocumentKey,
			"error", err,
		)
		return err
	}

	slog.Info("metadata updated successfully", "document_key", payload.DocumentKey)
	return nil
}
