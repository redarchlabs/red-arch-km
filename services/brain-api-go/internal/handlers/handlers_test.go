package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/pipeline"
)

// MockPipeline implements PipelineService for testing.
type MockPipeline struct {
	IngestResult      *pipeline.IngestResult
	IngestErr         error
	RemoveDocErr      error
	UpdateMetadataErr error
	InitTenantErr     error
	RemoveTenantErr   error
	SearchResults     []models.SearchResult
	SearchErr         error
	GraphResults      any
	GraphErr          error
	ChunksResults     []models.SearchResult
	ChunksErr         error
}

func (m *MockPipeline) IngestDocument(ctx context.Context, req pipeline.IngestRequest) (*pipeline.IngestResult, error) {
	if m.IngestErr != nil {
		return nil, m.IngestErr
	}
	if m.IngestResult != nil {
		return m.IngestResult, nil
	}
	return &pipeline.IngestResult{
		DocumentKey: req.DocumentKey,
		DocumentID:  "doc-123",
		Chunks:      5,
		Triplets:    3,
	}, nil
}

func (m *MockPipeline) RemoveDocument(ctx context.Context, tenantID, documentKey string) error {
	return m.RemoveDocErr
}

func (m *MockPipeline) UpdateMetadata(ctx context.Context, tenantID, documentKey string, tags []string, accessKeys []int, title *string) error {
	return m.UpdateMetadataErr
}

func (m *MockPipeline) InitTenant(ctx context.Context, tenantID string) error {
	return m.InitTenantErr
}

func (m *MockPipeline) RemoveTenant(ctx context.Context, tenantID string) error {
	return m.RemoveTenantErr
}

func (m *MockPipeline) Search(ctx context.Context, tenantID, query string, limit int, accessKeys []int, tags []string) ([]models.SearchResult, error) {
	if m.SearchErr != nil {
		return nil, m.SearchErr
	}
	return m.SearchResults, nil
}

func (m *MockPipeline) GraphSearch(ctx context.Context, tenantID, term, searchType string, tags []string, accessKeys []int) (any, error) {
	if m.GraphErr != nil {
		return nil, m.GraphErr
	}
	return m.GraphResults, nil
}

func (m *MockPipeline) GetDocumentChunks(ctx context.Context, tenantID, documentKey string, limit int) ([]models.SearchResult, error) {
	if m.ChunksErr != nil {
		return nil, m.ChunksErr
	}
	return m.ChunksResults, nil
}

// Verify MockPipeline implements PipelineService
var _ PipelineService = (*MockPipeline)(nil)

// Health endpoint tests
func TestHealthz(t *testing.T) {
	handler := Healthz()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}

	var resp HealthResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to parse response: %v", err)
	}

	if resp.Status != "ok" {
		t.Errorf("expected status 'ok', got %q", resp.Status)
	}
}

func TestReadyz_AllHealthy(t *testing.T) {
	deps := ReadyzDeps{
		QdrantHealthy: func() bool { return true },
		Neo4jHealthy:  func() bool { return true },
	}
	handler := Readyz(deps)
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
}

func TestReadyz_QdrantUnhealthy(t *testing.T) {
	deps := ReadyzDeps{
		QdrantHealthy: func() bool { return false },
		Neo4jHealthy:  func() bool { return true },
	}
	handler := Readyz(deps)
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected status 503, got %d", w.Code)
	}
}

func TestReadyz_NilChecks(t *testing.T) {
	deps := ReadyzDeps{}
	handler := Readyz(deps)
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200 with nil checks, got %d", w.Code)
	}
}

// IngestHandlers tests
func TestIngestDocument_Success(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1","document_key":"d1","title":"Test","text":"Content"}`
	req := httptest.NewRequest(http.MethodPost, "/ingest-document", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.IngestDocument().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestIngestDocument_InvalidJSON(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	req := httptest.NewRequest(http.MethodPost, "/ingest-document", bytes.NewBufferString("invalid"))
	w := httptest.NewRecorder()

	h.IngestDocument().ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}

func TestIngestDocument_MissingFields(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	tests := []struct {
		name string
		body string
	}{
		{"missing tenant_id", `{"document_key":"d1","title":"T","text":"C"}`},
		{"missing document_key", `{"tenant_id":"t1","title":"T","text":"C"}`},
		{"missing title", `{"tenant_id":"t1","document_key":"d1","text":"C"}`},
		{"missing text", `{"tenant_id":"t1","document_key":"d1","title":"T"}`},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, "/ingest-document", bytes.NewBufferString(tc.body))
			w := httptest.NewRecorder()

			h.IngestDocument().ServeHTTP(w, req)

			if w.Code != http.StatusBadRequest {
				t.Errorf("expected status 400, got %d", w.Code)
			}
		})
	}
}

func TestIngestDocument_PipelineError(t *testing.T) {
	mock := &MockPipeline{IngestErr: errors.New("failed")}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1","document_key":"d1","title":"Test","text":"Content"}`
	req := httptest.NewRequest(http.MethodPost, "/ingest-document", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.IngestDocument().ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

func TestRemoveDocument_Success(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1","document_key":"d1"}`
	req := httptest.NewRequest(http.MethodPost, "/remove-document", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.RemoveDocument().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
}

func TestRemoveDocument_MissingFields(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1"}`
	req := httptest.NewRequest(http.MethodPost, "/remove-document", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.RemoveDocument().ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}

func TestRemoveDocument_PipelineError(t *testing.T) {
	mock := &MockPipeline{RemoveDocErr: errors.New("failed")}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1","document_key":"d1"}`
	req := httptest.NewRequest(http.MethodPost, "/remove-document", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.RemoveDocument().ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

func TestUpdateDocumentMetadata_Success(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1","document_key":"d1","new_tags":["tag1"]}`
	req := httptest.NewRequest(http.MethodPost, "/update-document-metadata", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.UpdateDocumentMetadata().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
}

func TestUpdateDocumentMetadata_Error(t *testing.T) {
	mock := &MockPipeline{UpdateMetadataErr: errors.New("failed")}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1","document_key":"d1"}`
	req := httptest.NewRequest(http.MethodPost, "/update-document-metadata", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.UpdateDocumentMetadata().ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

func TestInitTenant_Success(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1"}`
	req := httptest.NewRequest(http.MethodPost, "/init-tenant", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.InitTenant().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
}

func TestInitTenant_MissingTenantID(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	body := `{}`
	req := httptest.NewRequest(http.MethodPost, "/init-tenant", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.InitTenant().ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}

func TestInitTenant_Error(t *testing.T) {
	mock := &MockPipeline{InitTenantErr: errors.New("failed")}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1"}`
	req := httptest.NewRequest(http.MethodPost, "/init-tenant", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.InitTenant().ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

func TestRemoveTenant_Success(t *testing.T) {
	mock := &MockPipeline{}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1"}`
	req := httptest.NewRequest(http.MethodPost, "/remove-tenant", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.RemoveTenant().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
}

func TestRemoveTenant_Error(t *testing.T) {
	mock := &MockPipeline{RemoveTenantErr: errors.New("failed")}
	h := NewIngestHandlers(mock)

	body := `{"tenant_id":"t1"}`
	req := httptest.NewRequest(http.MethodPost, "/remove-tenant", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.RemoveTenant().ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

func TestGetDocumentChunks_Success(t *testing.T) {
	mock := &MockPipeline{
		ChunksResults: []models.SearchResult{
			{ID: "c1", Payload: map[string]any{"text": "chunk1", "chunk_order": float64(0)}},
		},
	}
	h := NewIngestHandlers(mock)

	r := chi.NewRouter()
	r.Get("/documents/{tenant}/{key}/chunks", h.GetDocumentChunks())

	req := httptest.NewRequest(http.MethodGet, "/documents/t1/d1/chunks", nil)
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGetDocumentChunks_WithLimit(t *testing.T) {
	mock := &MockPipeline{ChunksResults: []models.SearchResult{}}
	h := NewIngestHandlers(mock)

	r := chi.NewRouter()
	r.Get("/documents/{tenant}/{key}/chunks", h.GetDocumentChunks())

	req := httptest.NewRequest(http.MethodGet, "/documents/t1/d1/chunks?limit=100", nil)
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}
}

func TestGetDocumentChunks_Error(t *testing.T) {
	mock := &MockPipeline{ChunksErr: errors.New("failed")}
	h := NewIngestHandlers(mock)

	r := chi.NewRouter()
	r.Get("/documents/{tenant}/{key}/chunks", h.GetDocumentChunks())

	req := httptest.NewRequest(http.MethodGet, "/documents/t1/d1/chunks", nil)
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

// SearchHandlers tests
func TestSearch_Success(t *testing.T) {
	mock := &MockPipeline{
		SearchResults: []models.SearchResult{
			{ID: "r1", Score: 0.9, Payload: map[string]any{"text": "result", "chunk_order": float64(0)}},
		},
	}
	h := NewSearchHandlers(mock)

	body := `{"tenant_id":"t1","query":"search term","limit":5}`
	req := httptest.NewRequest(http.MethodPost, "/search", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.Search().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestSearch_InvalidJSON(t *testing.T) {
	mock := &MockPipeline{}
	h := NewSearchHandlers(mock)

	req := httptest.NewRequest(http.MethodPost, "/search", bytes.NewBufferString("invalid"))
	w := httptest.NewRecorder()

	h.Search().ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}

func TestSearch_MissingFields(t *testing.T) {
	mock := &MockPipeline{}
	h := NewSearchHandlers(mock)

	tests := []struct {
		name string
		body string
	}{
		{"missing tenant_id", `{"query":"q"}`},
		{"missing query", `{"tenant_id":"t1"}`},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, "/search", bytes.NewBufferString(tc.body))
			w := httptest.NewRecorder()

			h.Search().ServeHTTP(w, req)

			if w.Code != http.StatusBadRequest {
				t.Errorf("expected status 400, got %d", w.Code)
			}
		})
	}
}

func TestSearch_DefaultLimit(t *testing.T) {
	mock := &MockPipeline{SearchResults: []models.SearchResult{}}
	h := NewSearchHandlers(mock)

	body := `{"tenant_id":"t1","query":"q","limit":-1}`
	req := httptest.NewRequest(http.MethodPost, "/search", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.Search().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200 (limit defaults to 5), got %d", w.Code)
	}
}

func TestSearch_Error(t *testing.T) {
	mock := &MockPipeline{SearchErr: errors.New("failed")}
	h := NewSearchHandlers(mock)

	body := `{"tenant_id":"t1","query":"q"}`
	req := httptest.NewRequest(http.MethodPost, "/search", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.Search().ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

func TestGraphSearch_Success(t *testing.T) {
	mock := &MockPipeline{GraphResults: []models.Entity{{Name: "Entity1"}}}
	h := NewSearchHandlers(mock)

	body := `{"tenant_id":"t1","term":"entity","search_type":"entity"}`
	req := httptest.NewRequest(http.MethodPost, "/graph-search", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.GraphSearch().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGraphSearch_DefaultSearchType(t *testing.T) {
	mock := &MockPipeline{GraphResults: []models.Entity{}}
	h := NewSearchHandlers(mock)

	body := `{"tenant_id":"t1","term":"entity"}`
	req := httptest.NewRequest(http.MethodPost, "/graph-search", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.GraphSearch().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200 (search_type defaults to entity), got %d", w.Code)
	}
}

func TestGraphSearch_InvalidSearchType(t *testing.T) {
	mock := &MockPipeline{}
	h := NewSearchHandlers(mock)

	body := `{"tenant_id":"t1","term":"entity","search_type":"invalid"}`
	req := httptest.NewRequest(http.MethodPost, "/graph-search", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.GraphSearch().ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}

func TestGraphSearch_Error(t *testing.T) {
	mock := &MockPipeline{GraphErr: errors.New("failed")}
	h := NewSearchHandlers(mock)

	body := `{"tenant_id":"t1","term":"entity","search_type":"entity"}`
	req := httptest.NewRequest(http.MethodPost, "/graph-search", bytes.NewBufferString(body))
	w := httptest.NewRecorder()

	h.GraphSearch().ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected status 500, got %d", w.Code)
	}
}

// Utility function tests
func TestJSONResponse(t *testing.T) {
	w := httptest.NewRecorder()
	data := map[string]string{"status": "ok"}

	jsonResponse(w, data, http.StatusOK)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}

	if ct := w.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected content-type application/json, got %q", ct)
	}
}

func TestJSONError(t *testing.T) {
	w := httptest.NewRecorder()

	jsonError(w, "test error", http.StatusBadRequest)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", w.Code)
	}
}

func TestToString(t *testing.T) {
	tests := []struct {
		input    any
		expected string
	}{
		{"hello", "hello"},
		{123, ""},
		{nil, ""},
	}

	for _, tc := range tests {
		result := toString(tc.input)
		if result != tc.expected {
			t.Errorf("toString(%v) = %q, want %q", tc.input, result, tc.expected)
		}
	}
}

func TestParseIntParam(t *testing.T) {
	var out int
	_, err := parseIntParam("42", &out)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if out != 42 {
		t.Errorf("expected 42, got %d", out)
	}

	_, err = parseIntParam("invalid", &out)
	if err == nil {
		t.Error("expected error for invalid int")
	}
}
