// Package client provides HTTP clients for external services.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// BrainAPIClient is a client for the brain-api service.
type BrainAPIClient struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

// BrainAPIConfig holds configuration for the BrainAPIClient.
type BrainAPIConfig struct {
	BaseURL string
	APIKey  string
	Timeout time.Duration
}

// NewBrainAPIClient creates a new BrainAPIClient.
func NewBrainAPIClient(cfg BrainAPIConfig) *BrainAPIClient {
	timeout := cfg.Timeout
	if timeout == 0 {
		timeout = 60 * time.Second
	}

	return &BrainAPIClient{
		baseURL: cfg.BaseURL,
		apiKey:  cfg.APIKey,
		httpClient: &http.Client{
			Timeout: timeout,
		},
	}
}

// tenantRequest is the JSON payload for tenant operations.
type tenantRequest struct {
	TenantID string `json:"tenant_id"`
}

// tenantResponse is the JSON response from tenant operations.
type tenantResponse struct {
	Status  string `json:"status"`
	Message string `json:"message,omitempty"`
}

// InitTenant initializes a tenant in the vector/graph stores.
func (c *BrainAPIClient) InitTenant(ctx context.Context, tenantID string) error {
	return c.doTenantRequest(ctx, "/api/init-tenant", tenantID)
}

// RemoveTenant removes a tenant's data from the vector/graph stores.
func (c *BrainAPIClient) RemoveTenant(ctx context.Context, tenantID string) error {
	return c.doTenantRequest(ctx, "/api/remove-tenant", tenantID)
}

// doTenantRequest performs a tenant operation request.
func (c *BrainAPIClient) doTenantRequest(ctx context.Context, path, tenantID string) error {
	payload := tenantRequest{TenantID: tenantID}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("execute request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("brain-api error (status %d): %s", resp.StatusCode, string(respBody))
	}

	return nil
}
