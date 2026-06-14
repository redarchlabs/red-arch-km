package handlers

import (
	"encoding/json"
	"log/slog"
	"net/http"
)

// SearchHandlers contains HTTP handlers for search operations.
type SearchHandlers struct {
	pipeline PipelineService
}

// NewSearchHandlers creates new search handlers.
func NewSearchHandlers(p PipelineService) *SearchHandlers {
	return &SearchHandlers{pipeline: p}
}

// VectorSearchRequest is the request body for vector search.
type VectorSearchRequest struct {
	TenantID   string   `json:"tenant_id"`
	Query      string   `json:"query"`
	Limit      int      `json:"limit"`
	AccessKeys []int    `json:"access_keys"`
	Tags       []string `json:"tags"`
}

// Search handles POST /search.
func (h *SearchHandlers) Search() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req VectorSearchRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "invalid request body", http.StatusBadRequest)
			return
		}

		if req.TenantID == "" {
			jsonError(w, "tenant_id is required", http.StatusBadRequest)
			return
		}
		if req.Query == "" {
			jsonError(w, "query is required", http.StatusBadRequest)
			return
		}
		if req.Limit <= 0 || req.Limit > 50 {
			req.Limit = 5
		}

		results, err := h.pipeline.Search(r.Context(), req.TenantID, req.Query, req.Limit, req.AccessKeys, req.Tags)
		if err != nil {
			slog.Error("search failed", "error", err)
			jsonError(w, "search failed", http.StatusInternalServerError)
			return
		}

		// Format results
		type searchResult struct {
			ID            string  `json:"id"`
			Score         float32 `json:"score"`
			Text          string  `json:"text"`
			Summary       string  `json:"summary"`
			ChunkOrder    int     `json:"chunk_order"`
			DocumentKey   string  `json:"document_key"`
			DocumentTitle string  `json:"document_title"`
		}

		items := make([]searchResult, len(results))
		for i, r := range results {
			order := 0
			if co, ok := r.Payload["chunk_order"].(float64); ok {
				order = int(co)
			}
			items[i] = searchResult{
				ID:            r.ID,
				Score:         r.Score,
				Text:          toString(r.Payload["text"]),
				Summary:       toString(r.Payload["summary"]),
				ChunkOrder:    order,
				DocumentKey:   toString(r.Payload["document_key"]),
				DocumentTitle: toString(r.Payload["document_title"]),
			}
		}

		jsonResponse(w, map[string]any{
			"results": items,
		}, http.StatusOK)
	}
}

// GraphSearchRequest is the request body for graph search.
type GraphSearchRequest struct {
	TenantID   string   `json:"tenant_id"`
	Term       string   `json:"term"`
	SearchType string   `json:"search_type"` // "entity" or "relationship"
	AccessKeys []int    `json:"access_keys"`
	Tags       []string `json:"tags"`
}

// GraphSearch handles POST /graph-search.
func (h *SearchHandlers) GraphSearch() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req GraphSearchRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "invalid request body", http.StatusBadRequest)
			return
		}

		if req.TenantID == "" {
			jsonError(w, "tenant_id is required", http.StatusBadRequest)
			return
		}
		if req.Term == "" {
			jsonError(w, "term is required", http.StatusBadRequest)
			return
		}
		if req.SearchType == "" {
			req.SearchType = "entity"
		}
		if req.SearchType != "entity" && req.SearchType != "relationship" {
			jsonError(w, "search_type must be 'entity' or 'relationship'", http.StatusBadRequest)
			return
		}

		results, err := h.pipeline.GraphSearch(r.Context(), req.TenantID, req.Term, req.SearchType, req.Tags, req.AccessKeys)
		if err != nil {
			slog.Error("graph search failed", "error", err)
			jsonError(w, "graph search failed", http.StatusInternalServerError)
			return
		}

		jsonResponse(w, map[string]any{
			"results": results,
		}, http.StatusOK)
	}
}
