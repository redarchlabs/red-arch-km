package handlers

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// newStatusRequest builds a POST request carrying the given documentID chi URL
// param and raw JSON body. A pointerless nil pool is used because every case
// here must be rejected during validation, before any database access.
func newStatusRequest(documentID, body string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/api/internal/documents/"+documentID+"/status", strings.NewReader(body))
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("documentID", documentID)
	return r.WithContext(context.WithValue(r.Context(), chi.RouteCtxKey, rctx))
}

// TestUpdateDocumentStatus_ValidationRejections covers every request that must
// be rejected BEFORE the handler touches the database. These run in the
// db-less CI test-go job; the happy path (204) and 404 live in the
// integration-tagged test that QA runs against a real Postgres.
func TestUpdateDocumentStatus_ValidationRejections(t *testing.T) {
	validTenant := uuid.New().String()

	tests := []struct {
		name           string
		documentID     string
		body           string
		expectedStatus int
	}{
		{
			name:           "invalid document id",
			documentID:     "not-a-uuid",
			body:           `{"tenant_id":"` + validTenant + `","status":"SUCCESS"}`,
			expectedStatus: http.StatusBadRequest,
		},
		{
			name:           "malformed json body",
			documentID:     uuid.New().String(),
			body:           `{"tenant_id": "` + validTenant + `", "status":`,
			expectedStatus: http.StatusBadRequest,
		},
		{
			name:           "missing tenant_id",
			documentID:     uuid.New().String(),
			body:           `{"status":"SUCCESS"}`,
			expectedStatus: http.StatusBadRequest,
		},
		{
			name:           "invalid tenant_id",
			documentID:     uuid.New().String(),
			body:           `{"tenant_id":"not-a-uuid","status":"SUCCESS"}`,
			expectedStatus: http.StatusBadRequest,
		},
		{
			name:           "missing status",
			documentID:     uuid.New().String(),
			body:           `{"tenant_id":"` + validTenant + `"}`,
			expectedStatus: http.StatusBadRequest,
		},
		{
			name:           "invalid status enum",
			documentID:     uuid.New().String(),
			body:           `{"tenant_id":"` + validTenant + `","status":"BOGUS"}`,
			expectedStatus: http.StatusBadRequest,
		},
		{
			name:           "lowercase status rejected",
			documentID:     uuid.New().String(),
			body:           `{"tenant_id":"` + validTenant + `","status":"success"}`,
			expectedStatus: http.StatusBadRequest,
		},
	}

	// nil pool: not reached on any validation-rejection path.
	h := NewInternalHandler(nil)

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			h.UpdateDocumentStatus(w, newStatusRequest(tt.documentID, tt.body))

			if w.Code != tt.expectedStatus {
				t.Errorf("status = %d, want %d (body=%q)", w.Code, tt.expectedStatus, w.Body.String())
			}
		})
	}
}

// TestValidDocumentStatuses locks the accepted enum to the worker-reported set
// (parity with worker-go tasks.Status* + the Python DocumentStatusUpdate
// pattern). PENDING is included for the api-go default/back-compat.
func TestValidDocumentStatuses(t *testing.T) {
	for _, s := range []string{"PENDING", "PROCESSING", "SUCCESS", "FAILED"} {
		if !validDocumentStatuses[s] {
			t.Errorf("expected %q to be an accepted status", s)
		}
	}
	for _, s := range []string{"pending", "Success", "DONE", "", "ERROR"} {
		if validDocumentStatuses[s] {
			t.Errorf("did not expect %q to be an accepted status", s)
		}
	}
}
