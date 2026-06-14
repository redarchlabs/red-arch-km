package middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
)

func TestRequireOrg(t *testing.T) {
	validUUID := uuid.New()

	tests := []struct {
		name           string
		headerValue    string
		expectedStatus int
		expectOrgID    bool
	}{
		{
			name:           "valid UUID",
			headerValue:    validUUID.String(),
			expectedStatus: http.StatusOK,
			expectOrgID:    true,
		},
		{
			name:           "missing header",
			headerValue:    "",
			expectedStatus: http.StatusBadRequest,
			expectOrgID:    false,
		},
		{
			name:           "invalid UUID",
			headerValue:    "not-a-uuid",
			expectedStatus: http.StatusBadRequest,
			expectOrgID:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var capturedOrgID uuid.UUID
			var hasOrgID bool

			handler := RequireOrg(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				capturedOrgID, hasOrgID = GetOrgID(r.Context())
				w.WriteHeader(http.StatusOK)
			}))

			r := httptest.NewRequest("GET", "/", nil)
			if tt.headerValue != "" {
				r.Header.Set("X-Org-ID", tt.headerValue)
			}
			w := httptest.NewRecorder()

			handler.ServeHTTP(w, r)

			if w.Code != tt.expectedStatus {
				t.Errorf("status = %d, want %d", w.Code, tt.expectedStatus)
			}
			if tt.expectOrgID {
				if !hasOrgID {
					t.Error("expected org ID in context")
				}
				if capturedOrgID != validUUID {
					t.Errorf("org ID = %v, want %v", capturedOrgID, validUUID)
				}
			}
		})
	}
}

func TestOptionalOrg(t *testing.T) {
	validUUID := uuid.New()

	tests := []struct {
		name           string
		headerValue    string
		expectedStatus int
		expectOrgID    bool
	}{
		{
			name:           "valid UUID",
			headerValue:    validUUID.String(),
			expectedStatus: http.StatusOK,
			expectOrgID:    true,
		},
		{
			name:           "missing header",
			headerValue:    "",
			expectedStatus: http.StatusOK,
			expectOrgID:    false,
		},
		{
			name:           "invalid UUID",
			headerValue:    "not-a-uuid",
			expectedStatus: http.StatusBadRequest,
			expectOrgID:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var hasOrgID bool

			handler := OptionalOrg(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, hasOrgID = GetOrgID(r.Context())
				w.WriteHeader(http.StatusOK)
			}))

			r := httptest.NewRequest("GET", "/", nil)
			if tt.headerValue != "" {
				r.Header.Set("X-Org-ID", tt.headerValue)
			}
			w := httptest.NewRecorder()

			handler.ServeHTTP(w, r)

			if w.Code != tt.expectedStatus {
				t.Errorf("status = %d, want %d", w.Code, tt.expectedStatus)
			}
			if tt.expectOrgID && !hasOrgID {
				t.Error("expected org ID in context")
			}
			if !tt.expectOrgID && hasOrgID {
				t.Error("did not expect org ID in context")
			}
		})
	}
}

func TestGetOrgID(t *testing.T) {
	// Test with no org ID
	ctx := context.Background()
	_, ok := GetOrgID(ctx)
	if ok {
		t.Error("expected no org ID in empty context")
	}

	// Test with org ID
	orgID := uuid.New()
	ctx = context.WithValue(ctx, orgIDKey, orgID)
	got, ok := GetOrgID(ctx)
	if !ok {
		t.Fatal("expected org ID in context")
	}
	if got != orgID {
		t.Errorf("org ID = %v, want %v", got, orgID)
	}
}

func TestMustGetOrgID(t *testing.T) {
	// Test panic without org ID
	defer func() {
		if r := recover(); r == nil {
			t.Error("expected panic for missing org ID")
		}
	}()
	ctx := context.Background()
	MustGetOrgID(ctx)
}
