package handlers

import (
	"context"
	"errors"
	"log/slog"

	"github.com/hibiken/asynq"

	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/client"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/tasks"
)

// RemoveHandler handles document removal tasks.
type RemoveHandler struct {
	brainClient client.BrainClient
}

// NewRemoveHandler creates a new remove handler.
func NewRemoveHandler(brainClient client.BrainClient) *RemoveHandler {
	return &RemoveHandler{
		brainClient: brainClient,
	}
}

// ProcessTask processes a remove task.
func (h *RemoveHandler) ProcessTask(ctx context.Context, task *asynq.Task) error {
	payload, err := tasks.ParseRemovePayload(task)
	if err != nil {
		slog.Error("failed to parse remove payload", "error", err)
		return nil // Don't retry malformed payloads
	}

	slog.Info("processing remove task",
		"document_key", payload.DocumentKey,
		"tenant_id", payload.TenantID,
	)

	if err := h.brainClient.RemoveDocument(ctx, payload.TenantID, payload.DocumentKey); err != nil {
		var httpErr *client.HTTPError
		if errors.As(err, &httpErr) {
			if httpErr.IsRetryable() {
				slog.Warn("transient brain-api error on remove, will retry",
					"document_key", payload.DocumentKey,
					"status_code", httpErr.StatusCode,
				)
				return err // Retry
			}
			// Non-retryable error (e.g., 404 — document already gone)
			slog.Warn("non-retryable error removing document",
				"document_key", payload.DocumentKey,
				"status_code", httpErr.StatusCode,
			)
			return nil
		}
		// Network error — retry
		slog.Warn("network error removing document, will retry",
			"document_key", payload.DocumentKey,
			"error", err,
		)
		return err
	}

	slog.Info("document removed successfully", "document_key", payload.DocumentKey)
	return nil
}
