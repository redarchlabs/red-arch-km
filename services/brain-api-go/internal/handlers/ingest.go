package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/pipeline"
)

// IngestHandlers contains HTTP handlers for document ingestion.
type IngestHandlers struct {
	pipeline PipelineService
}

// NewIngestHandlers creates new ingest handlers.
func NewIngestHandlers(p PipelineService) *IngestHandlers {
	return &IngestHandlers{pipeline: p}
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

// IngestDocument handles POST /ingest-document.
func (h *IngestHandlers) IngestDocument() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req IngestDocumentRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "invalid request body", http.StatusBadRequest)
			return
		}

		// Validate required fields
		if req.TenantID == "" {
			jsonError(w, "tenant_id is required", http.StatusBadRequest)
			return
		}
		if req.DocumentKey == "" {
			jsonError(w, "document_key is required", http.StatusBadRequest)
			return
		}
		if req.Title == "" {
			jsonError(w, "title is required", http.StatusBadRequest)
			return
		}
		if req.Text == "" {
			jsonError(w, "text is required", http.StatusBadRequest)
			return
		}

		result, err := h.pipeline.IngestDocument(r.Context(), pipeline.IngestRequest{
			TenantID:          req.TenantID,
			DocumentKey:       req.DocumentKey,
			Title:             req.Title,
			Text:              req.Text,
			Tags:              req.Tags,
			AccessKeys:        req.AccessKeys,
			UseKnowledgeGraph: req.UseKnowledgeGraph,
			Metadata:          req.Metadata,
		})
		if err != nil {
			slog.Error("ingestion failed", "document_key", req.DocumentKey, "error", err)
			jsonError(w, "document ingestion failed", http.StatusInternalServerError)
			return
		}

		jsonResponse(w, result, http.StatusOK)
	}
}

// RemoveDocumentRequest is the request body for document removal.
type RemoveDocumentRequest struct {
	TenantID    string `json:"tenant_id"`
	DocumentKey string `json:"document_key"`
}

// RemoveDocument handles POST /remove-document.
func (h *IngestHandlers) RemoveDocument() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req RemoveDocumentRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "invalid request body", http.StatusBadRequest)
			return
		}

		if req.TenantID == "" || req.DocumentKey == "" {
			jsonError(w, "tenant_id and document_key are required", http.StatusBadRequest)
			return
		}

		if err := h.pipeline.RemoveDocument(r.Context(), req.TenantID, req.DocumentKey); err != nil {
			slog.Error("remove document failed", "document_key", req.DocumentKey, "error", err)
			jsonError(w, "failed to remove document", http.StatusInternalServerError)
			return
		}

		jsonResponse(w, map[string]string{
			"status":       "deleted",
			"document_key": req.DocumentKey,
		}, http.StatusOK)
	}
}

// UpdateMetadataRequest is the request body for metadata updates.
type UpdateMetadataRequest struct {
	TenantID      string   `json:"tenant_id"`
	DocumentKey   string   `json:"document_key"`
	Title         *string  `json:"title,omitempty"`
	NewTags       []string `json:"new_tags,omitempty"`
	NewAccessKeys []int    `json:"new_access_keys,omitempty"`
}

// UpdateDocumentMetadata handles POST /update-document-metadata.
func (h *IngestHandlers) UpdateDocumentMetadata() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req UpdateMetadataRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "invalid request body", http.StatusBadRequest)
			return
		}

		if req.TenantID == "" || req.DocumentKey == "" {
			jsonError(w, "tenant_id and document_key are required", http.StatusBadRequest)
			return
		}

		if err := h.pipeline.UpdateMetadata(r.Context(), req.TenantID, req.DocumentKey, req.NewTags, req.NewAccessKeys, req.Title); err != nil {
			slog.Error("update metadata failed", "document_key", req.DocumentKey, "error", err)
			jsonError(w, "failed to update metadata", http.StatusInternalServerError)
			return
		}

		jsonResponse(w, map[string]string{"status": "updated"}, http.StatusOK)
	}
}

// TenantRequest is the request body for tenant operations.
type TenantRequest struct {
	TenantID string `json:"tenant_id"`
}

// InitTenant handles POST /init-tenant.
func (h *IngestHandlers) InitTenant() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req TenantRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "invalid request body", http.StatusBadRequest)
			return
		}

		if req.TenantID == "" {
			jsonError(w, "tenant_id is required", http.StatusBadRequest)
			return
		}

		if err := h.pipeline.InitTenant(r.Context(), req.TenantID); err != nil {
			slog.Error("init tenant failed", "tenant_id", req.TenantID, "error", err)
			jsonError(w, "tenant initialization failed", http.StatusInternalServerError)
			return
		}

		jsonResponse(w, map[string]string{
			"status":    "initialized",
			"tenant_id": req.TenantID,
		}, http.StatusOK)
	}
}

// RemoveTenant handles POST /remove-tenant.
func (h *IngestHandlers) RemoveTenant() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req TenantRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "invalid request body", http.StatusBadRequest)
			return
		}

		if req.TenantID == "" {
			jsonError(w, "tenant_id is required", http.StatusBadRequest)
			return
		}

		if err := h.pipeline.RemoveTenant(r.Context(), req.TenantID); err != nil {
			slog.Error("remove tenant failed", "tenant_id", req.TenantID, "error", err)
			jsonError(w, "tenant removal failed", http.StatusInternalServerError)
			return
		}

		jsonResponse(w, map[string]string{
			"status":    "removed",
			"tenant_id": req.TenantID,
		}, http.StatusOK)
	}
}

// GetDocumentChunks handles GET /documents/{tenant}/{key}/chunks.
func (h *IngestHandlers) GetDocumentChunks() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		tenantID := chi.URLParam(r, "tenant")
		documentKey := chi.URLParam(r, "key")

		if tenantID == "" || documentKey == "" {
			jsonError(w, "tenant and document key are required", http.StatusBadRequest)
			return
		}

		limit := 500 // default
		if l := r.URL.Query().Get("limit"); l != "" {
			var lv int
			if _, err := parseIntParam(l, &lv); err == nil && lv > 0 && lv <= 1000 {
				limit = lv
			}
		}

		chunks, err := h.pipeline.GetDocumentChunks(r.Context(), tenantID, documentKey, limit)
		if err != nil {
			slog.Error("get chunks failed", "document_key", documentKey, "error", err)
			jsonError(w, "failed to fetch chunks", http.StatusInternalServerError)
			return
		}

		type chunkItem struct {
			ID         string `json:"id"`
			Text       string `json:"text"`
			ChunkOrder int    `json:"chunk_order"`
		}

		items := make([]chunkItem, len(chunks))
		for i, c := range chunks {
			order := 0
			if co, ok := c.Payload["chunk_order"].(float64); ok {
				order = int(co)
			}
			items[i] = chunkItem{
				ID:         c.ID,
				Text:       toString(c.Payload["text"]),
				ChunkOrder: order,
			}
		}

		jsonResponse(w, map[string]any{
			"document_key": documentKey,
			"chunks":       items,
		}, http.StatusOK)
	}
}

func toString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func parseIntParam(s string, out *int) (int, error) {
	var v int
	_, err := parseIntFromString(s, &v)
	if err != nil {
		return 0, err
	}
	*out = v
	return v, nil
}

func parseIntFromString(s string, out *int) (int, error) {
	var v int
	n, err := json.Number(s).Int64()
	if err != nil {
		return 0, err
	}
	v = int(n)
	*out = v
	return v, nil
}
