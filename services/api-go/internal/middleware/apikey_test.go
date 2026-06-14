package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAPIKeyAuth(t *testing.T) {
	validKey := "test-api-key-123"

	tests := []struct {
		name           string
		headerValue    string
		expectedStatus int
	}{
		{
			name:           "valid key",
			headerValue:    validKey,
			expectedStatus: http.StatusOK,
		},
		{
			name:           "missing key",
			headerValue:    "",
			expectedStatus: http.StatusUnauthorized,
		},
		{
			name:           "invalid key",
			headerValue:    "wrong-key",
			expectedStatus: http.StatusForbidden,
		},
		{
			name:           "partial match",
			headerValue:    "test-api-key-12",
			expectedStatus: http.StatusForbidden,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			handler := APIKeyAuth(validKey)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusOK)
			}))

			r := httptest.NewRequest("GET", "/", nil)
			if tt.headerValue != "" {
				r.Header.Set("X-API-Key", tt.headerValue)
			}
			w := httptest.NewRecorder()

			handler.ServeHTTP(w, r)

			if w.Code != tt.expectedStatus {
				t.Errorf("status = %d, want %d", w.Code, tt.expectedStatus)
			}
		})
	}
}
