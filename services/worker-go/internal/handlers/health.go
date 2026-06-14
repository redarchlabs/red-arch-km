package handlers

import (
	"encoding/json"
	"net/http"
)

// HealthDeps contains dependencies for health checks.
type HealthDeps struct {
	QueueHealthy func() bool
}

// Healthz returns a simple liveness probe handler.
func Healthz() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}
}

// Readyz returns a readiness probe handler that checks dependencies.
func Readyz(deps HealthDeps) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		healthy := true
		checks := map[string]string{}

		if deps.QueueHealthy != nil {
			if deps.QueueHealthy() {
				checks["redis"] = "ok"
			} else {
				checks["redis"] = "error"
				healthy = false
			}
		}

		w.Header().Set("Content-Type", "application/json")
		if healthy {
			w.WriteHeader(http.StatusOK)
			checks["status"] = "ok"
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			checks["status"] = "error"
		}
		json.NewEncoder(w).Encode(checks)
	}
}
