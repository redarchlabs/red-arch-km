package handlers

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/hibiken/asynq"

	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/client"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/tasks"
)

// mockBrainClient implements client.BrainClient for testing.
type mockBrainClient struct {
	ingestFunc   func(ctx context.Context, req client.IngestDocumentRequest) (*client.IngestDocumentResponse, error)
	removeFunc   func(ctx context.Context, tenantID, documentKey string) error
	metadataFunc func(ctx context.Context, req client.UpdateMetadataRequest) error
}

func (m *mockBrainClient) IngestDocument(ctx context.Context, req client.IngestDocumentRequest) (*client.IngestDocumentResponse, error) {
	if m.ingestFunc != nil {
		return m.ingestFunc(ctx, req)
	}
	return &client.IngestDocumentResponse{Chunks: 5, Triplets: 3}, nil
}

func (m *mockBrainClient) RemoveDocument(ctx context.Context, tenantID, documentKey string) error {
	if m.removeFunc != nil {
		return m.removeFunc(ctx, tenantID, documentKey)
	}
	return nil
}

func (m *mockBrainClient) UpdateMetadata(ctx context.Context, req client.UpdateMetadataRequest) error {
	if m.metadataFunc != nil {
		return m.metadataFunc(ctx, req)
	}
	return nil
}

// mockAPIClient implements client.APIClient for testing.
type mockAPIClient struct {
	reportFunc func(ctx context.Context, documentID, tenantID, status string, details map[string]any) error
	reported   []statusReport
}

type statusReport struct {
	documentID string
	tenantID   string
	status     string
	details    map[string]any
}

func (m *mockAPIClient) ReportDocumentStatus(ctx context.Context, documentID, tenantID, status string, details map[string]any) error {
	m.reported = append(m.reported, statusReport{documentID, tenantID, status, details})
	if m.reportFunc != nil {
		return m.reportFunc(ctx, documentID, tenantID, status, details)
	}
	return nil
}

// createTestTask creates an asynq task for testing.
func createIngestTask(t *testing.T, payload tasks.IngestPayload) *asynq.Task {
	t.Helper()
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("failed to marshal payload: %v", err)
	}
	return asynq.NewTask(tasks.TypeIngestDocument, data)
}

func createRemoveTask(t *testing.T, payload tasks.RemovePayload) *asynq.Task {
	t.Helper()
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("failed to marshal payload: %v", err)
	}
	return asynq.NewTask(tasks.TypeRemoveDocument, data)
}

func createMetadataTask(t *testing.T, payload tasks.UpdateMetadataPayload) *asynq.Task {
	t.Helper()
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("failed to marshal payload: %v", err)
	}
	return asynq.NewTask(tasks.TypeUpdateMetadata, data)
}

func TestIngestHandler_ProcessTask(t *testing.T) {
	t.Run("successful ingestion", func(t *testing.T) {
		brainClient := &mockBrainClient{
			ingestFunc: func(ctx context.Context, req client.IngestDocumentRequest) (*client.IngestDocumentResponse, error) {
				if req.TenantID != "tenant-123" {
					t.Errorf("unexpected tenant ID: %s", req.TenantID)
				}
				return &client.IngestDocumentResponse{Chunks: 10, Triplets: 5}, nil
			},
		}
		apiClient := &mockAPIClient{}

		handler := NewIngestHandler(brainClient, apiClient)
		task := createIngestTask(t, tasks.IngestPayload{
			DocumentID:        "doc-123",
			TenantID:          "tenant-123",
			DocumentKey:       "key-789",
			Title:             "Test",
			Text:              "Content",
			Tags:              []string{"tag1"},
			AccessKeys:        []int{1},
			UseKnowledgeGraph: true,
		})

		err := handler.ProcessTask(context.Background(), task)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		// Should report PROCESSING and SUCCESS
		if len(apiClient.reported) != 2 {
			t.Fatalf("expected 2 status reports, got %d", len(apiClient.reported))
		}
		if apiClient.reported[0].status != tasks.StatusProcessing {
			t.Errorf("expected first status to be PROCESSING, got %s", apiClient.reported[0].status)
		}
		if apiClient.reported[1].status != tasks.StatusSuccess {
			t.Errorf("expected second status to be SUCCESS, got %s", apiClient.reported[1].status)
		}
	})

	t.Run("retryable error", func(t *testing.T) {
		brainClient := &mockBrainClient{
			ingestFunc: func(ctx context.Context, req client.IngestDocumentRequest) (*client.IngestDocumentResponse, error) {
				return nil, &client.HTTPError{StatusCode: 500, Body: "internal error"}
			},
		}
		apiClient := &mockAPIClient{}

		handler := NewIngestHandler(brainClient, apiClient)
		task := createIngestTask(t, tasks.IngestPayload{
			DocumentID:  "doc-123",
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err == nil {
			t.Fatal("expected error for retry")
		}

		// With plain context, GetRetryCount returns 0 (first attempt) and GetMaxRetry returns 0,
		// so 0 >= 0 is true, meaning this is both first AND last attempt — reports PROCESSING + FAILED
		if len(apiClient.reported) < 1 {
			t.Fatalf("expected at least 1 status report, got %d", len(apiClient.reported))
		}
		if apiClient.reported[0].status != tasks.StatusProcessing {
			t.Errorf("expected first status to be PROCESSING, got %s", apiClient.reported[0].status)
		}
	})

	t.Run("permanent error", func(t *testing.T) {
		brainClient := &mockBrainClient{
			ingestFunc: func(ctx context.Context, req client.IngestDocumentRequest) (*client.IngestDocumentResponse, error) {
				return nil, &client.HTTPError{StatusCode: 400, Body: "bad request"}
			},
		}
		apiClient := &mockAPIClient{}

		handler := NewIngestHandler(brainClient, apiClient)
		task := createIngestTask(t, tasks.IngestPayload{
			DocumentID:  "doc-123",
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err != nil {
			t.Fatalf("expected no error (permanent errors should not retry), got %v", err)
		}

		// Should have reported PROCESSING and FAILED
		if len(apiClient.reported) != 2 {
			t.Fatalf("expected 2 status reports, got %d", len(apiClient.reported))
		}
		if apiClient.reported[1].status != tasks.StatusFailed {
			t.Errorf("expected second status to be FAILED, got %s", apiClient.reported[1].status)
		}
	})

	t.Run("invalid payload", func(t *testing.T) {
		handler := NewIngestHandler(&mockBrainClient{}, &mockAPIClient{})
		task := asynq.NewTask(tasks.TypeIngestDocument, []byte("invalid json"))

		err := handler.ProcessTask(context.Background(), task)
		if err == nil {
			t.Fatal("expected error for invalid payload")
		}
	})
}

func TestRemoveHandler_ProcessTask(t *testing.T) {
	t.Run("successful removal", func(t *testing.T) {
		removed := false
		brainClient := &mockBrainClient{
			removeFunc: func(ctx context.Context, tenantID, documentKey string) error {
				if tenantID != "tenant-123" {
					t.Errorf("unexpected tenant ID: %s", tenantID)
				}
				if documentKey != "key-789" {
					t.Errorf("unexpected document key: %s", documentKey)
				}
				removed = true
				return nil
			},
		}

		handler := NewRemoveHandler(brainClient)
		task := createRemoveTask(t, tasks.RemovePayload{
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !removed {
			t.Error("expected remove to be called")
		}
	})

	t.Run("retryable error", func(t *testing.T) {
		brainClient := &mockBrainClient{
			removeFunc: func(ctx context.Context, tenantID, documentKey string) error {
				return &client.HTTPError{StatusCode: 503, Body: "service unavailable"}
			},
		}

		handler := NewRemoveHandler(brainClient)
		task := createRemoveTask(t, tasks.RemovePayload{
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err == nil {
			t.Fatal("expected error for retry")
		}
	})

	t.Run("not found (non-retryable)", func(t *testing.T) {
		brainClient := &mockBrainClient{
			removeFunc: func(ctx context.Context, tenantID, documentKey string) error {
				return &client.HTTPError{StatusCode: 404, Body: "not found"}
			},
		}

		handler := NewRemoveHandler(brainClient)
		task := createRemoveTask(t, tasks.RemovePayload{
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err != nil {
			t.Fatalf("expected no error (404 should not retry), got %v", err)
		}
	})

	t.Run("network error", func(t *testing.T) {
		brainClient := &mockBrainClient{
			removeFunc: func(ctx context.Context, tenantID, documentKey string) error {
				return errors.New("connection refused")
			},
		}

		handler := NewRemoveHandler(brainClient)
		task := createRemoveTask(t, tasks.RemovePayload{
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err == nil {
			t.Fatal("expected error for retry")
		}
	})
}

func TestMetadataHandler_ProcessTask(t *testing.T) {
	t.Run("successful update", func(t *testing.T) {
		updated := false
		brainClient := &mockBrainClient{
			metadataFunc: func(ctx context.Context, req client.UpdateMetadataRequest) error {
				if req.TenantID != "tenant-123" {
					t.Errorf("unexpected tenant ID: %s", req.TenantID)
				}
				updated = true
				return nil
			},
		}

		handler := NewMetadataHandler(brainClient)
		title := "New Title"
		task := createMetadataTask(t, tasks.UpdateMetadataPayload{
			TenantID:      "tenant-123",
			DocumentKey:   "key-789",
			Title:         &title,
			NewTags:       []string{"tag1"},
			NewAccessKeys: []int{1, 2},
		})

		err := handler.ProcessTask(context.Background(), task)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !updated {
			t.Error("expected update to be called")
		}
	})

	t.Run("retryable error", func(t *testing.T) {
		brainClient := &mockBrainClient{
			metadataFunc: func(ctx context.Context, req client.UpdateMetadataRequest) error {
				return &client.HTTPError{StatusCode: 500, Body: "internal error"}
			},
		}

		handler := NewMetadataHandler(brainClient)
		task := createMetadataTask(t, tasks.UpdateMetadataPayload{
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err == nil {
			t.Fatal("expected error for retry")
		}
	})

	t.Run("permanent error", func(t *testing.T) {
		brainClient := &mockBrainClient{
			metadataFunc: func(ctx context.Context, req client.UpdateMetadataRequest) error {
				return &client.HTTPError{StatusCode: 400, Body: "bad request"}
			},
		}

		handler := NewMetadataHandler(brainClient)
		task := createMetadataTask(t, tasks.UpdateMetadataPayload{
			TenantID:    "tenant-123",
			DocumentKey: "key-789",
		})

		err := handler.ProcessTask(context.Background(), task)
		if err != nil {
			t.Fatalf("expected no error (permanent errors should not retry), got %v", err)
		}
	})

	t.Run("invalid payload", func(t *testing.T) {
		handler := NewMetadataHandler(&mockBrainClient{})
		task := asynq.NewTask(tasks.TypeUpdateMetadata, []byte("invalid json"))

		// Invalid payload should not retry — just log and return nil
		err := handler.ProcessTask(context.Background(), task)
		if err != nil {
			t.Fatalf("expected nil for invalid payload (don't retry), got %v", err)
		}
	})
}
