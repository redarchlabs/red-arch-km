package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestBrainAPIClient_InitTenant(t *testing.T) {
	tests := []struct {
		name       string
		tenantID   string
		statusCode int
		wantErr    bool
	}{
		{
			name:       "success",
			tenantID:   "test-tenant-123",
			statusCode: http.StatusOK,
			wantErr:    false,
		},
		{
			name:       "server error",
			tenantID:   "test-tenant-456",
			statusCode: http.StatusInternalServerError,
			wantErr:    true,
		},
		{
			name:       "not found",
			tenantID:   "test-tenant-789",
			statusCode: http.StatusNotFound,
			wantErr:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/api/init-tenant" {
					t.Errorf("unexpected path: %s", r.URL.Path)
				}
				if r.Method != http.MethodPost {
					t.Errorf("unexpected method: %s", r.Method)
				}
				if r.Header.Get("X-API-Key") != "test-api-key" {
					t.Errorf("missing or wrong API key")
				}
				if r.Header.Get("Content-Type") != "application/json" {
					t.Errorf("wrong content type")
				}

				var req tenantRequest
				if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
					t.Errorf("decode request: %v", err)
				}
				if req.TenantID != tt.tenantID {
					t.Errorf("wrong tenant ID: got %s, want %s", req.TenantID, tt.tenantID)
				}

				w.WriteHeader(tt.statusCode)
				if tt.statusCode == http.StatusOK {
					json.NewEncoder(w).Encode(tenantResponse{Status: "ok"})
				} else {
					w.Write([]byte("error"))
				}
			}))
			defer server.Close()

			client := NewBrainAPIClient(BrainAPIConfig{
				BaseURL: server.URL,
				APIKey:  "test-api-key",
				Timeout: 5 * time.Second,
			})

			err := client.InitTenant(context.Background(), tt.tenantID)
			if (err != nil) != tt.wantErr {
				t.Errorf("InitTenant() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestBrainAPIClient_RemoveTenant(t *testing.T) {
	tests := []struct {
		name       string
		tenantID   string
		statusCode int
		wantErr    bool
	}{
		{
			name:       "success",
			tenantID:   "test-tenant-123",
			statusCode: http.StatusOK,
			wantErr:    false,
		},
		{
			name:       "server error",
			tenantID:   "test-tenant-456",
			statusCode: http.StatusInternalServerError,
			wantErr:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/api/remove-tenant" {
					t.Errorf("unexpected path: %s", r.URL.Path)
				}

				w.WriteHeader(tt.statusCode)
				if tt.statusCode == http.StatusOK {
					json.NewEncoder(w).Encode(tenantResponse{Status: "ok"})
				} else {
					w.Write([]byte("error"))
				}
			}))
			defer server.Close()

			client := NewBrainAPIClient(BrainAPIConfig{
				BaseURL: server.URL,
				APIKey:  "test-api-key",
			})

			err := client.RemoveTenant(context.Background(), tt.tenantID)
			if (err != nil) != tt.wantErr {
				t.Errorf("RemoveTenant() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestNewBrainAPIClient_DefaultTimeout(t *testing.T) {
	client := NewBrainAPIClient(BrainAPIConfig{
		BaseURL: "http://example.com",
		APIKey:  "key",
	})

	if client.httpClient.Timeout != 60*time.Second {
		t.Errorf("expected default timeout of 60s, got %v", client.httpClient.Timeout)
	}
}
