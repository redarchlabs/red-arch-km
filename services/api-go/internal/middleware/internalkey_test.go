package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestInternalAPIKeyAuth(t *testing.T) {
	const validKey = "internal-secret-abc123"

	tests := []struct {
		name           string
		configuredKey  string
		headerSet      bool
		headerValue    string
		expectedStatus int
		expectNext     bool
	}{
		{
			name:           "valid key passes through",
			configuredKey:  validKey,
			headerSet:      true,
			headerValue:    validKey,
			expectedStatus: http.StatusOK,
			expectNext:     true,
		},
		{
			name:           "unconfigured key disables endpoint (503) even with header",
			configuredKey:  "",
			headerSet:      true,
			headerValue:    "anything",
			expectedStatus: http.StatusServiceUnavailable,
			expectNext:     false,
		},
		{
			name:           "missing header is unauthorized",
			configuredKey:  validKey,
			headerSet:      false,
			expectedStatus: http.StatusUnauthorized,
			expectNext:     false,
		},
		{
			name:           "empty header value is unauthorized",
			configuredKey:  validKey,
			headerSet:      true,
			headerValue:    "",
			expectedStatus: http.StatusUnauthorized,
			expectNext:     false,
		},
		{
			name:           "wrong key is unauthorized",
			configuredKey:  validKey,
			headerSet:      true,
			headerValue:    "wrong-key",
			expectedStatus: http.StatusUnauthorized,
			expectNext:     false,
		},
		{
			name:           "partial match is unauthorized",
			configuredKey:  validKey,
			headerSet:      true,
			headerValue:    validKey[:len(validKey)-1],
			expectedStatus: http.StatusUnauthorized,
			expectNext:     false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			nextCalled := false
			handler := InternalAPIKeyAuth(tt.configuredKey)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				nextCalled = true
				w.WriteHeader(http.StatusOK)
			}))

			r := httptest.NewRequest(http.MethodPost, "/api/internal/documents/x/status", nil)
			if tt.headerSet {
				r.Header.Set("X-Internal-API-Key", tt.headerValue)
			}
			w := httptest.NewRecorder()

			handler.ServeHTTP(w, r)

			if w.Code != tt.expectedStatus {
				t.Errorf("status = %d, want %d", w.Code, tt.expectedStatus)
			}
			if nextCalled != tt.expectNext {
				t.Errorf("next called = %v, want %v", nextCalled, tt.expectNext)
			}
		})
	}
}

// TestInternalAPIKeyAuthDoesNotReadBrainKeyHeader ensures the internal-key
// middleware uses its OWN header and does not accept the brain-api X-API-Key
// (separate-secret invariant).
func TestInternalAPIKeyAuthDoesNotReadBrainKeyHeader(t *testing.T) {
	handler := InternalAPIKeyAuth("internal-secret")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	r := httptest.NewRequest(http.MethodPost, "/", nil)
	r.Header.Set("X-API-Key", "internal-secret") // wrong header on purpose
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, r)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want %d (brain X-API-Key must not satisfy internal auth)", w.Code, http.StatusUnauthorized)
	}
}
