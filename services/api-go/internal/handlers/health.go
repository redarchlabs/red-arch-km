// Package handlers provides HTTP request handlers.
package handlers

import (
	"encoding/json"
	"net/http"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
)

// HealthResponse represents a health check response.
type HealthResponse struct {
	Status string `json:"status"`
	DB     string `json:"db,omitempty"`
}

// Healthz returns a liveness probe handler (always returns 200).
func Healthz() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(HealthResponse{Status: "ok"})
	}
}

// Readyz returns a readiness probe handler that checks DB connectivity.
// A nil pool (e.g. DATABASE_URL not configured) is treated as not ready:
// reporting 200 in that case would let a misconfigured deploy pass
// readiness checks with database-backed features silently disabled.
func Readyz(pool *db.Pool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		if pool == nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(HealthResponse{
				Status: "error",
				DB:     "not_configured",
			})
			return
		}

		if err := pool.Health(r.Context()); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(HealthResponse{
				Status: "error",
				DB:     "unreachable",
			})
			return
		}

		json.NewEncoder(w).Encode(HealthResponse{
			Status: "ok",
			DB:     "connected",
		})
	}
}
