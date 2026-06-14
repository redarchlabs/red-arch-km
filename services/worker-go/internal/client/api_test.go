package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAPIClient_ReportDocumentStatus(t *testing.T) {
	t.Run("successful status report", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/api/internal/documents/doc-123/status" {
				t.Errorf("unexpected path: %s", r.URL.Path)
			}
			if r.Method != http.MethodPost {
				t.Errorf("unexpected method: %s", r.Method)
			}
			if r.Header.Get("X-Internal-API-Key") != "test-key" {
				t.Errorf("unexpected API key: %s", r.Header.Get("X-Internal-API-Key"))
			}

			var req StatusUpdateRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Fatalf("failed to decode request: %v", err)
			}

			if req.TenantID != "tenant-456" {
				t.Errorf("unexpected tenant ID: %s", req.TenantID)
			}
			if req.Status != "SUCCESS" {
				t.Errorf("unexpected status: %s", req.Status)
			}

			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
		}))
		defer server.Close()

		client := NewAPIClient(server.URL, "test-key")
		err := client.ReportDocumentStatus(context.Background(), "doc-123", "tenant-456", "SUCCESS", map[string]any{
			"chunks":   10,
			"triplets": 5,
		})

		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("skip if no document ID", func(t *testing.T) {
		client := NewAPIClient("http://localhost:8000", "test-key")
		err := client.ReportDocumentStatus(context.Background(), "", "tenant-456", "SUCCESS", nil)

		// Should return nil without making a request
		if err != nil {
			t.Errorf("expected nil, got %v", err)
		}
	})

	t.Run("skip if no API key", func(t *testing.T) {
		client := NewAPIClient("http://localhost:8000", "")
		err := client.ReportDocumentStatus(context.Background(), "doc-123", "tenant-456", "SUCCESS", nil)

		// Should return nil without making a request
		if err != nil {
			t.Errorf("expected nil, got %v", err)
		}
	})

	t.Run("server error", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
		}))
		defer server.Close()

		client := NewAPIClient(server.URL, "test-key")
		err := client.ReportDocumentStatus(context.Background(), "doc-123", "tenant-456", "SUCCESS", nil)

		if err == nil {
			t.Fatal("expected error")
		}
	})

	t.Run("with details", func(t *testing.T) {
		var receivedDetails map[string]any

		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			var req StatusUpdateRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Fatalf("failed to decode request: %v", err)
			}
			receivedDetails = req.Details
			w.WriteHeader(http.StatusOK)
		}))
		defer server.Close()

		client := NewAPIClient(server.URL, "test-key")
		err := client.ReportDocumentStatus(context.Background(), "doc-123", "tenant-456", "FAILED", map[string]any{
			"error":   "network",
			"message": "connection refused",
		})

		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}

		if receivedDetails["error"] != "network" {
			t.Errorf("expected error=network, got %v", receivedDetails["error"])
		}
	})
}
