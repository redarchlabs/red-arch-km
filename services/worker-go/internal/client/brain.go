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

// BrainClient is an HTTP client for the brain-api service.
type BrainClient interface {
	IngestDocument(ctx context.Context, req IngestDocumentRequest) (*IngestDocumentResponse, error)
	RemoveDocument(ctx context.Context, tenantID, documentKey string) error
	UpdateMetadata(ctx context.Context, req UpdateMetadataRequest) error
}

// brainClient implements BrainClient.
type brainClient struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

// NewBrainClient creates a new brain-api client.
func NewBrainClient(baseURL, apiKey string) BrainClient {
	return &brainClient{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{
			Timeout: 5 * time.Minute, // Ingestion can take a while
		},
	}
}

// IngestDocumentRequest is the request body for document ingestion.
type IngestDocumentRequest struct {
	TenantID          string         `json:"tenant_id"`
	DocumentKey       string         `json:"document_key"`
	Title             string         `json:"title"`
	Text              string         `json:"text"`
	Tags              []string       `json:"tags"`
	AccessKeys        []int          `json:"access_keys"`
	UseKnowledgeGraph bool           `json:"use_knowledge_graph"`
	Metadata          map[string]any `json:"metadata"`
}

// IngestDocumentResponse is the response from document ingestion.
type IngestDocumentResponse struct {
	Chunks   int `json:"chunks"`
	Triplets int `json:"triplets"`
}

// UpdateMetadataRequest is the request body for metadata updates.
type UpdateMetadataRequest struct {
	TenantID      string   `json:"tenant_id"`
	DocumentKey   string   `json:"document_key"`
	Title         *string  `json:"title,omitempty"`
	NewTags       []string `json:"new_tags,omitempty"`
	NewAccessKeys []int    `json:"new_access_keys,omitempty"`
}

// HTTPError represents an HTTP error response.
type HTTPError struct {
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("HTTP %d: %s", e.StatusCode, e.Body)
}

// IsRetryable returns true if the error is retryable (5xx or 429).
func (e *HTTPError) IsRetryable() bool {
	return e.StatusCode >= 500 || e.StatusCode == 429
}

// IngestDocument sends a document for ingestion to brain-api.
func (c *brainClient) IngestDocument(ctx context.Context, req IngestDocumentRequest) (*IngestDocumentResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/ingest-document", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		httpReq.Header.Set("X-API-Key", c.apiKey)
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("do request: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)

	if resp.StatusCode >= 400 {
		return nil, &HTTPError{
			StatusCode: resp.StatusCode,
			Body:       string(respBody),
		}
	}

	var result IngestDocumentResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("unmarshal response: %w", err)
	}

	return &result, nil
}

// RemoveDocument removes a document from brain-api.
func (c *brainClient) RemoveDocument(ctx context.Context, tenantID, documentKey string) error {
	body, err := json.Marshal(map[string]string{
		"tenant_id":    tenantID,
		"document_key": documentKey,
	})
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/remove-document", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		httpReq.Header.Set("X-API-Key", c.apiKey)
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return fmt.Errorf("do request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		respBody, _ := io.ReadAll(resp.Body)
		return &HTTPError{
			StatusCode: resp.StatusCode,
			Body:       string(respBody),
		}
	}

	return nil
}

// UpdateMetadata updates document metadata in brain-api.
func (c *brainClient) UpdateMetadata(ctx context.Context, req UpdateMetadataRequest) error {
	body, err := json.Marshal(req)
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/update-document-metadata", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" {
		httpReq.Header.Set("X-API-Key", c.apiKey)
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return fmt.Errorf("do request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		respBody, _ := io.ReadAll(resp.Body)
		return &HTTPError{
			StatusCode: resp.StatusCode,
			Body:       string(respBody),
		}
	}

	return nil
}
