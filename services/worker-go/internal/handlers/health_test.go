package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthz(t *testing.T) {
	handler := Healthz()

	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", w.Code)
	}

	var resp map[string]string
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp["status"] != "ok" {
		t.Errorf("expected status=ok, got %s", resp["status"])
	}
}

func TestReadyz(t *testing.T) {
	t.Run("all healthy", func(t *testing.T) {
		handler := Readyz(HealthDeps{
			QueueHealthy: func() bool { return true },
		})

		req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
		w := httptest.NewRecorder()

		handler.ServeHTTP(w, req)

		if w.Code != http.StatusOK {
			t.Errorf("expected status 200, got %d", w.Code)
		}

		var resp map[string]string
		if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
			t.Fatalf("failed to decode response: %v", err)
		}

		if resp["status"] != "ok" {
			t.Errorf("expected status=ok, got %s", resp["status"])
		}
		if resp["redis"] != "ok" {
			t.Errorf("expected redis=ok, got %s", resp["redis"])
		}
	})

	t.Run("queue unhealthy", func(t *testing.T) {
		handler := Readyz(HealthDeps{
			QueueHealthy: func() bool { return false },
		})

		req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
		w := httptest.NewRecorder()

		handler.ServeHTTP(w, req)

		if w.Code != http.StatusServiceUnavailable {
			t.Errorf("expected status 503, got %d", w.Code)
		}

		var resp map[string]string
		if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
			t.Fatalf("failed to decode response: %v", err)
		}

		if resp["status"] != "error" {
			t.Errorf("expected status=error, got %s", resp["status"])
		}
		if resp["redis"] != "error" {
			t.Errorf("expected redis=error, got %s", resp["redis"])
		}
	})

	t.Run("no queue check configured", func(t *testing.T) {
		handler := Readyz(HealthDeps{})

		req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
		w := httptest.NewRecorder()

		handler.ServeHTTP(w, req)

		if w.Code != http.StatusOK {
			t.Errorf("expected status 200, got %d", w.Code)
		}

		var resp map[string]string
		if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
			t.Fatalf("failed to decode response: %v", err)
		}

		if resp["status"] != "ok" {
			t.Errorf("expected status=ok, got %s", resp["status"])
		}
		// Redis should not be in response if not configured
		if _, exists := resp["redis"]; exists {
			t.Errorf("expected redis to not be in response")
		}
	})
}
