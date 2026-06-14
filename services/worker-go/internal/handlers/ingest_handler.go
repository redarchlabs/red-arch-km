// Package handlers contains task handlers for the worker.
package handlers

import (
	"context"
	"errors"
	"log/slog"

	"github.com/hibiken/asynq"

	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/client"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/tasks"
)

// IngestHandler handles document ingestion tasks.
type IngestHandler struct {
	brainClient client.BrainClient
	apiClient   client.APIClient
}

// NewIngestHandler creates a new ingest handler.
func NewIngestHandler(brainClient client.BrainClient, apiClient client.APIClient) *IngestHandler {
	return &IngestHandler{
		brainClient: brainClient,
		apiClient:   apiClient,
	}
}

// ProcessTask processes an ingest task.
func (h *IngestHandler) ProcessTask(ctx context.Context, task *asynq.Task) error {
	payload, err := tasks.ParseIngestPayload(task)
	if err != nil {
		slog.Error("failed to parse ingest payload", "error", err)
		return err // Don't retry malformed payloads
	}

	slog.Info("processing ingest task",
		"document_key", payload.DocumentKey,
		"tenant_id", payload.TenantID,
	)

	// Report PROCESSING status (best-effort)
	retried, _ := asynq.GetRetryCount(ctx)
	if retried == 0 {
		if err := h.apiClient.ReportDocumentStatus(ctx, payload.DocumentID, payload.TenantID, tasks.StatusProcessing, nil); err != nil {
			slog.Warn("failed to report processing status", "document_id", payload.DocumentID, "error", err)
		}
	}

	// Call brain-api to ingest the document
	result, err := h.brainClient.IngestDocument(ctx, client.IngestDocumentRequest{
		TenantID:          payload.TenantID,
		DocumentKey:       payload.DocumentKey,
		Title:             payload.Title,
		Text:              payload.Text,
		Tags:              payload.Tags,
		AccessKeys:        payload.AccessKeys,
		UseKnowledgeGraph: payload.UseKnowledgeGraph,
		Metadata:          payload.Metadata,
	})

	if err != nil {
		var httpErr *client.HTTPError
		if errors.As(err, &httpErr) {
			if httpErr.IsRetryable() {
				slog.Warn("transient brain-api error, will retry",
					"document_key", payload.DocumentKey,
					"status_code", httpErr.StatusCode,
				)
				// Check if this is the last retry
				maxRetry, _ := asynq.GetMaxRetry(ctx)
				if retried >= maxRetry {
					h.apiClient.ReportDocumentStatus(ctx, payload.DocumentID, payload.TenantID, tasks.StatusFailed, map[string]any{
						"error":             "HTTP " + string(rune(httpErr.StatusCode)),
						"retries_exhausted": true,
					})
				}
				return err // Retry
			}
			// Non-retryable HTTP error
			slog.Error("permanent brain-api error",
				"document_key", payload.DocumentKey,
				"status_code", httpErr.StatusCode,
				"body", httpErr.Body,
			)
			h.apiClient.ReportDocumentStatus(ctx, payload.DocumentID, payload.TenantID, tasks.StatusFailed, map[string]any{
				"error": httpErr.Error(),
			})
			return nil // Don't retry permanent errors
		}

		// Network error — retry
		slog.Warn("network error ingesting document, will retry",
			"document_key", payload.DocumentKey,
			"error", err,
		)
		maxRetry, _ := asynq.GetMaxRetry(ctx)
		if retried >= maxRetry {
			h.apiClient.ReportDocumentStatus(ctx, payload.DocumentID, payload.TenantID, tasks.StatusFailed, map[string]any{
				"error":   "network",
				"message": err.Error(),
			})
		}
		return err
	}

	// Success
	slog.Info("document ingested successfully",
		"document_key", payload.DocumentKey,
		"chunks", result.Chunks,
		"triplets", result.Triplets,
	)

	h.apiClient.ReportDocumentStatus(ctx, payload.DocumentID, payload.TenantID, tasks.StatusSuccess, map[string]any{
		"chunks":   result.Chunks,
		"triplets": result.Triplets,
	})

	return nil
}
