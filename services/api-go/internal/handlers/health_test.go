package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthz(t *testing.T) {
	handler := Healthz()

	r := httptest.NewRequest("GET", "/healthz", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("status = %d, want %d", w.Code, http.StatusOK)
	}

	var resp HealthResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to unmarshal response: %v", err)
	}

	if resp.Status != "ok" {
		t.Errorf("status = %q, want %q", resp.Status, "ok")
	}
}

// TestReadyzNilPool verifies readyz reports 503 (not ready) when the pool is
// nil, e.g. DATABASE_URL was not configured. Reporting 200 in that case would
// let a misconfigured deploy pass readiness with DB features silently
// disabled.
func TestReadyzNilPool(t *testing.T) {
	handler := Readyz(nil)

	r := httptest.NewRequest("GET", "/readyz", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, r)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want %d", w.Code, http.StatusServiceUnavailable)
	}

	var resp HealthResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to unmarshal response: %v", err)
	}

	if resp.Status != "error" {
		t.Errorf("status = %q, want %q", resp.Status, "error")
	}
}

// Readyz tests with a live pool require a database connection, so they are
// integration tests.
