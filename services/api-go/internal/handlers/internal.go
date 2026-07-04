package handlers

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/httputil"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// validDocumentStatuses is the set of processing_status values accepted from a
// worker status callback. It mirrors the worker-go tasks.Status* constants and
// the Python DocumentStatusUpdate pattern (PENDING|PROCESSING|SUCCESS|FAILED).
// PENDING is included for the api-go CreateDocument default / back-compat.
var validDocumentStatuses = map[string]bool{
	"PENDING":    true,
	"PROCESSING": true,
	"SUCCESS":    true,
	"FAILED":     true,
}

// DocumentStatusUpdateRequest is a worker-reported processing status for a
// document. Field shape matches worker-go client.StatusUpdateRequest and the
// Python api.routers.internal.DocumentStatusUpdate.
type DocumentStatusUpdateRequest struct {
	// TenantID is the owning org id, used to set the RLS tenant scope.
	TenantID string `json:"tenant_id"`
	// Status is one of PENDING, PROCESSING, SUCCESS, FAILED.
	Status string `json:"status"`
	// Details is an optional structured blob (chunks/triplets/error). When
	// omitted, the document's existing processing_details are preserved.
	Details map[string]interface{} `json:"details"`
}

// InternalHandler handles internal service-to-service endpoints. These are NOT
// exposed to end users; they authenticate via a shared X-Internal-API-Key
// (see middleware.InternalAPIKeyAuth), not user JWTs.
type InternalHandler struct {
	pool *db.Pool
}

// NewInternalHandler creates a new InternalHandler.
func NewInternalHandler(pool *db.Pool) *InternalHandler {
	return &InternalHandler{pool: pool}
}

// UpdateDocumentStatus sets processing_status (and optionally
// processing_details) for a document on behalf of a trusted worker reporting
// ingestion progress.
//
// POST /api/internal/documents/{documentID}/status
//
// Auth is enforced upstream by InternalAPIKeyAuth. The tenant scope comes from
// the request body — the worker has no user JWT — and RLS enforces isolation:
// a tenant_id that does not own the document sees it filtered out and receives
// 404 (never a cross-tenant write). Mirrors the Python internal router.
//
// All request validation happens before any database access, so malformed
// requests are rejected deterministically and are unit-testable without a DB.
// On success returns 204 No Content.
func (h *InternalHandler) UpdateDocumentStatus(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	documentID, err := ParseUUID(chi.URLParam(r, "documentID"))
	if err != nil {
		httputil.BadRequest(w, "Invalid document ID")
		return
	}

	var req DocumentStatusUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httputil.BadRequest(w, "Invalid request body")
		return
	}

	tenantID, err := ParseUUID(req.TenantID)
	if err != nil {
		httputil.BadRequest(w, "Invalid or missing tenant_id")
		return
	}

	if !validDocumentStatuses[req.Status] {
		httputil.BadRequest(w, "Invalid status (must be one of PENDING, PROCESSING, SUCCESS, FAILED)")
		return
	}

	// --- validation complete; everything below requires the database ---

	// This is a read-then-write that must be atomic AND consistently
	// RLS-scoped. The tenant GUC is applied with set_config(..., is_local=true),
	// which only persists for the duration of an explicit transaction — so we
	// manage one here (rather than an autocommit connection) to guarantee that
	// both the GetDocument visibility check and the UpdateDocumentStatus write
	// observe the same tenant scope. The worker supplies the tenant (it has no
	// user JWT); RLS enforces isolation, so a tenant that does not own the
	// document sees it filtered out and receives 404 — never a cross-tenant
	// write.
	tx, err := h.pool.Begin(ctx)
	if err != nil {
		slog.Error("begin status-update tx", "error", err)
		httputil.InternalError(w, "")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck // rollback after a successful commit is a no-op

	if _, err := tx.Exec(ctx, "SELECT set_config('app.current_tenant_id', $1, true)", tenantID.String()); err != nil {
		slog.Error("set tenant context", "error", err)
		httputil.InternalError(w, "")
		return
	}

	queries := repository.New(tx)

	// Fetch first (RLS-scoped) so a document owned by another tenant, or one
	// deleted mid-processing, is reported as 404 rather than silently no-op'd.
	// Mirrors the Python get()-then-update semantics.
	doc, err := queries.GetDocument(ctx, ToPgUUID(documentID))
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			httputil.NotFound(w, "Document not found (may have been deleted)")
			return
		}
		slog.Error("get document for status update", "error", err)
		httputil.InternalError(w, "")
		return
	}

	// Preserve existing details when the caller omits them (the Python handler
	// only sets processing_details when details is non-null).
	detailsBytes := doc.ProcessingDetails
	if req.Details != nil {
		b, marshalErr := json.Marshal(req.Details)
		if marshalErr != nil {
			httputil.BadRequest(w, "Invalid details payload")
			return
		}
		detailsBytes = b
	}

	if err := queries.UpdateDocumentStatus(ctx, repository.UpdateDocumentStatusParams{
		ID:                ToPgUUID(documentID),
		ProcessingStatus:  pgtype.Text{String: req.Status, Valid: true},
		ProcessingDetails: detailsBytes,
	}); err != nil {
		slog.Error("update document status", "error", err, "document_id", documentID)
		httputil.InternalError(w, "")
		return
	}

	if err := tx.Commit(ctx); err != nil {
		slog.Error("commit status update", "error", err)
		httputil.InternalError(w, "")
		return
	}

	httputil.NoContent(w)
}
