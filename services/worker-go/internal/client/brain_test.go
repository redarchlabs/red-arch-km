package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestBrainClient_IngestDocument(t *testing.T) {
	t.Run("successful ingestion", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/ingest-document" {
				t.Errorf("unexpected path: %s", r.URL.Path)
			}
			if r.Method != http.MethodPost {
				t.Errorf("unexpected method: %s", r.Method)
			}
			if r.Header.Get("X-API-Key") != "test-key" {
				t.Errorf("unexpected API key: %s", r.Header.Get("X-API-Key"))
			}

			var req IngestDocumentRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Fatalf("failed to decode request: %v", err)
			}

			if req.TenantID != "tenant-123" {
				t.Errorf("unexpected tenant ID: %s", req.TenantID)
			}

			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(IngestDocumentResponse{
				Chunks:   10,
				Triplets: 5,
			})
		}))
		defer server.Close()

		client := NewBrainClient(server.URL, "test-key")
		result, err := client.IngestDocument(context.Background(), IngestDocumentRequest{
			TenantID:          "tenant-123",
			DocumentKey:       "doc-key",
			Title:             "Test",
			Text:              "Content",
			Tags:              []string{"tag1"},
			AccessKeys:        []int{1},
			UseKnowledgeGraph: true,
			Metadata:          map[string]any{},
		})

		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if result.Chunks != 10 {
			t.Errorf("expected 10 chunks, got %d", result.Chunks)
		}
		if result.Triplets != 5 {
			t.Errorf("expected 5 triplets, got %d", result.Triplets)
		}
	})

	t.Run("server error", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"error": "internal error"}`))
		}))
		defer server.Close()

		client := NewBrainClient(server.URL, "test-key")
		_, err := client.IngestDocument(context.Background(), IngestDocumentRequest{
			TenantID:    "tenant-123",
			DocumentKey: "doc-key",
		})

		if err == nil {
			t.Fatal("expected error")
		}

		httpErr, ok := err.(*HTTPError)
		if !ok {
			t.Fatalf("expected HTTPError, got %T", err)
		}
		if httpErr.StatusCode != 500 {
			t.Errorf("expected status 500, got %d", httpErr.StatusCode)
		}
		if !httpErr.IsRetryable() {
			t.Error("expected error to be retryable")
		}
	})

	t.Run("client error", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusBadRequest)
			w.Write([]byte(`{"error": "bad request"}`))
		}))
		defer server.Close()

		client := NewBrainClient(server.URL, "test-key")
		_, err := client.IngestDocument(context.Background(), IngestDocumentRequest{
			TenantID:    "tenant-123",
			DocumentKey: "doc-key",
		})

		if err == nil {
			t.Fatal("expected error")
		}

		httpErr, ok := err.(*HTTPError)
		if !ok {
			t.Fatalf("expected HTTPError, got %T", err)
		}
		if httpErr.StatusCode != 400 {
			t.Errorf("expected status 400, got %d", httpErr.StatusCode)
		}
		if httpErr.IsRetryable() {
			t.Error("expected error to NOT be retryable")
		}
	})
}

func TestBrainClient_RemoveDocument(t *testing.T) {
	t.Run("successful removal", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/remove-document" {
				t.Errorf("unexpected path: %s", r.URL.Path)
			}
			if r.Method != http.MethodPost {
				t.Errorf("unexpected method: %s", r.Method)
			}

			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
		}))
		defer server.Close()

		client := NewBrainClient(server.URL, "test-key")
		err := client.RemoveDocument(context.Background(), "tenant-123", "doc-key")

		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("not found", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNotFound)
			w.Write([]byte(`{"error": "not found"}`))
		}))
		defer server.Close()

		client := NewBrainClient(server.URL, "test-key")
		err := client.RemoveDocument(context.Background(), "tenant-123", "doc-key")

		if err == nil {
			t.Fatal("expected error")
		}

		httpErr, ok := err.(*HTTPError)
		if !ok {
			t.Fatalf("expected HTTPError, got %T", err)
		}
		if httpErr.StatusCode != 404 {
			t.Errorf("expected status 404, got %d", httpErr.StatusCode)
		}
	})
}

func TestBrainClient_UpdateMetadata(t *testing.T) {
	t.Run("successful update", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/update-document-metadata" {
				t.Errorf("unexpected path: %s", r.URL.Path)
			}
			if r.Method != http.MethodPost {
				t.Errorf("unexpected method: %s", r.Method)
			}

			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]string{"status": "updated"})
		}))
		defer server.Close()

		client := NewBrainClient(server.URL, "test-key")
		title := "New Title"
		err := client.UpdateMetadata(context.Background(), UpdateMetadataRequest{
			TenantID:      "tenant-123",
			DocumentKey:   "doc-key",
			Title:         &title,
			NewTags:       []string{"tag1"},
			NewAccessKeys: []int{1, 2},
		})

		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("rate limited", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusTooManyRequests)
			w.Write([]byte(`{"error": "rate limited"}`))
		}))
		defer server.Close()

		client := NewBrainClient(server.URL, "test-key")
		err := client.UpdateMetadata(context.Background(), UpdateMetadataRequest{
			TenantID:    "tenant-123",
			DocumentKey: "doc-key",
		})

		if err == nil {
			t.Fatal("expected error")
		}

		httpErr, ok := err.(*HTTPError)
		if !ok {
			t.Fatalf("expected HTTPError, got %T", err)
		}
		if httpErr.StatusCode != 429 {
			t.Errorf("expected status 429, got %d", httpErr.StatusCode)
		}
		if !httpErr.IsRetryable() {
			t.Error("expected error to be retryable")
		}
	})
}

func TestHTTPError(t *testing.T) {
	t.Run("error message", func(t *testing.T) {
		err := &HTTPError{StatusCode: 500, Body: "server error"}
		msg := err.Error()
		if msg != "HTTP 500: server error" {
			t.Errorf("unexpected error message: %s", msg)
		}
	})

	t.Run("retryable status codes", func(t *testing.T) {
		retryableCodes := []int{429, 500, 502, 503, 504}
		for _, code := range retryableCodes {
			err := &HTTPError{StatusCode: code}
			if !err.IsRetryable() {
				t.Errorf("expected %d to be retryable", code)
			}
		}
	})

	t.Run("non-retryable status codes", func(t *testing.T) {
		nonRetryableCodes := []int{400, 401, 403, 404, 422}
		for _, code := range nonRetryableCodes {
			err := &HTTPError{StatusCode: code}
			if err.IsRetryable() {
				t.Errorf("expected %d to NOT be retryable", code)
			}
		}
	})
}
