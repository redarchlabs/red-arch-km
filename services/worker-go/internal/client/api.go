package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// APIClient is an HTTP client for the main api service.
type APIClient interface {
	ReportDocumentStatus(ctx context.Context, documentID, tenantID, status string, details map[string]any) error
}

// apiClient implements APIClient.
type apiClient struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

// NewAPIClient creates a new api service client.
func NewAPIClient(baseURL, apiKey string) APIClient {
	return &apiClient{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{
			Timeout: 15 * time.Second,
		},
	}
}

// StatusUpdateRequest is the request body for status updates.
type StatusUpdateRequest struct {
	TenantID string         `json:"tenant_id"`
	Status   string         `json:"status"`
	Details  map[string]any `json:"details,omitempty"`
}

// ReportDocumentStatus reports document processing status to the API.
// This is best-effort — errors are logged but not propagated.
func (c *apiClient) ReportDocumentStatus(ctx context.Context, documentID, tenantID, status string, details map[string]any) error {
	if documentID == "" || c.apiKey == "" {
		return nil // Skip if we can't report
	}

	body, err := json.Marshal(StatusUpdateRequest{
		TenantID: tenantID,
		Status:   status,
		Details:  details,
	})
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}

	url := fmt.Sprintf("%s/api/internal/documents/%s/status", c.baseURL, documentID)
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("X-Internal-API-Key", c.apiKey)

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return fmt.Errorf("do request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return fmt.Errorf("status update failed: HTTP %d", resp.StatusCode)
	}

	return nil
}
