// Package handlers provides HTTP request handlers for brain-api.
package handlers

import (
	"encoding/json"
	"net/http"
)

// HealthResponse represents a health check response.
type HealthResponse struct {
	Status string `json:"status"`
	Qdrant string `json:"qdrant,omitempty"`
	Neo4j  string `json:"neo4j,omitempty"`
}

// Healthz returns a liveness probe handler.
func Healthz() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(HealthResponse{Status: "ok"})
	}
}

// ReadyzDeps holds dependencies for readiness check.
type ReadyzDeps struct {
	QdrantHealthy func() bool
	Neo4jHealthy  func() bool
}

// Readyz returns a readiness probe handler that checks Qdrant and Neo4j.
func Readyz(deps ReadyzDeps) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		resp := HealthResponse{Status: "ok"}
		healthy := true

		if deps.QdrantHealthy != nil {
			if deps.QdrantHealthy() {
				resp.Qdrant = "connected"
			} else {
				resp.Qdrant = "unreachable"
				healthy = false
			}
		}

		if deps.Neo4jHealthy != nil {
			if deps.Neo4jHealthy() {
				resp.Neo4j = "connected"
			} else {
				resp.Neo4j = "unreachable"
				healthy = false
			}
		}

		if !healthy {
			resp.Status = "error"
			w.WriteHeader(http.StatusServiceUnavailable)
		}

		json.NewEncoder(w).Encode(resp)
	}
}
