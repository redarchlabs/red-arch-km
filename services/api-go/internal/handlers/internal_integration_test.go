//go:build integration

package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// Integration tests for the internal document-status callback. They require a
// migrated PostgreSQL instance (documents/orgs tables + RLS policies) and are
// gated behind the `integration` build tag so the db-less CI test-go job skips
// them. Run (in the MAIN worktree, per the live-QA protocol):
//
//	DATABASE_URL=postgres://... go test -tags=integration -race \
//	  ./services/api-go/internal/handlers/ -run TestUpdateDocumentStatus_Integration

func openTestPool(t *testing.T) *db.Pool {
	t.Helper()
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		t.Skip("DATABASE_URL not set; skipping integration test")
	}
	pool, err := db.NewPool(context.Background(), dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	return pool
}

// seedOrg inserts a fresh tenant org (orgs is not RLS-scoped).
func seedOrg(t *testing.T, pool *db.Pool) uuid.UUID {
	t.Helper()
	orgID := uuid.New()
	q := repository.New(pool)
	if _, err := q.CreateOrg(context.Background(), repository.CreateOrgParams{
		ID:                ToPgUUID(orgID),
		Name:              "t3-internal-" + orgID.String()[:8],
		UseKnowledgeGraph: pgtype.Bool{Bool: false, Valid: true},
		PermissionNumber:  pgtype.Int2{Int16: 0, Valid: true},
	}); err != nil {
		t.Fatalf("seed org: %v", err)
	}
	t.Cleanup(func() {
		// ON DELETE CASCADE removes the org's documents.
		_, _ = pool.Exec(context.Background(), "DELETE FROM orgs WHERE id = $1", orgID)
	})
	return orgID
}

// seedDocument inserts a document owned by orgID within a tenant-scoped tx.
func seedDocument(t *testing.T, pool *db.Pool, orgID uuid.UUID) uuid.UUID {
	t.Helper()
	docID := uuid.New()
	ctx := context.Background()
	tx, err := pool.Begin(ctx)
	if err != nil {
		t.Fatalf("begin seed tx: %v", err)
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SELECT set_config('app.current_tenant_id', $1, true)", orgID.String()); err != nil {
		t.Fatalf("set tenant: %v", err)
	}
	q := repository.New(tx)
	if _, err := q.CreateDocument(ctx, repository.CreateDocumentParams{
		ID:               ToPgUUID(docID),
		Title:            "seed doc",
		DocumentKey:      "seed-" + docID.String(),
		ProcessingStatus: pgtype.Text{String: "PENDING", Valid: true},
		OrgID:            ToPgUUID(orgID),
	}); err != nil {
		t.Fatalf("seed document: %v", err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("commit seed: %v", err)
	}
	return docID
}

// readDoc reads a document RLS-scoped to orgID; returns (status, details, found).
func readDoc(t *testing.T, pool *db.Pool, orgID, docID uuid.UUID) (string, map[string]interface{}, bool) {
	t.Helper()
	ctx := context.Background()
	tx, err := pool.Begin(ctx)
	if err != nil {
		t.Fatalf("begin read tx: %v", err)
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SELECT set_config('app.current_tenant_id', $1, true)", orgID.String()); err != nil {
		t.Fatalf("set tenant: %v", err)
	}
	doc, err := repository.New(tx).GetDocument(ctx, ToPgUUID(docID))
	if err != nil {
		return "", nil, false
	}
	var details map[string]interface{}
	if len(doc.ProcessingDetails) > 0 {
		_ = json.Unmarshal(doc.ProcessingDetails, &details)
	}
	return doc.ProcessingStatus.String, details, true
}

func callStatus(t *testing.T, h *InternalHandler, docID uuid.UUID, body string) int {
	t.Helper()
	w := httptest.NewRecorder()
	h.UpdateDocumentStatus(w, newStatusRequest(docID.String(), body))
	return w.Code
}

func TestUpdateDocumentStatus_Integration(t *testing.T) {
	pool := openTestPool(t)
	defer pool.Close()

	orgID := seedOrg(t, pool)
	docID := seedDocument(t, pool, orgID)
	h := NewInternalHandler(pool)

	// 1. Happy path: SUCCESS with details -> 204, persisted.
	if code := callStatus(t, h, docID, `{"tenant_id":"`+orgID.String()+`","status":"SUCCESS","details":{"chunks":3}}`); code != http.StatusNoContent {
		t.Fatalf("happy path: status = %d, want 204", code)
	}
	status, details, found := readDoc(t, pool, orgID, docID)
	if !found || status != "SUCCESS" {
		t.Fatalf("after SUCCESS: found=%v status=%q, want SUCCESS", found, status)
	}
	if details == nil || details["chunks"].(float64) != 3 {
		t.Fatalf("after SUCCESS: details = %v, want chunks=3", details)
	}

	// 2. Details preservation: omit details -> status changes, details kept.
	if code := callStatus(t, h, docID, `{"tenant_id":"`+orgID.String()+`","status":"PROCESSING"}`); code != http.StatusNoContent {
		t.Fatalf("preserve path: status = %d, want 204", code)
	}
	status, details, _ = readDoc(t, pool, orgID, docID)
	if status != "PROCESSING" {
		t.Fatalf("after PROCESSING: status = %q, want PROCESSING", status)
	}
	if details == nil || details["chunks"].(float64) != 3 {
		t.Fatalf("after PROCESSING: details = %v, want preserved chunks=3", details)
	}

	// 3. Cross-tenant: another org may not touch org1's document -> 404, unchanged.
	otherOrg := seedOrg(t, pool)
	if code := callStatus(t, h, docID, `{"tenant_id":"`+otherOrg.String()+`","status":"FAILED"}`); code != http.StatusNotFound {
		t.Fatalf("cross-tenant: status = %d, want 404", code)
	}
	status, _, _ = readDoc(t, pool, orgID, docID)
	if status != "PROCESSING" {
		t.Fatalf("cross-tenant leaked a write: status = %q, want unchanged PROCESSING", status)
	}

	// 4. Unknown document -> 404.
	if code := callStatus(t, h, uuid.New(), `{"tenant_id":"`+orgID.String()+`","status":"FAILED"}`); code != http.StatusNotFound {
		t.Fatalf("unknown doc: status = %d, want 404", code)
	}
}
