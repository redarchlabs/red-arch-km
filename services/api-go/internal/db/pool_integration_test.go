//go:build integration

package db

import (
	"context"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// Integration tests for WithTenant's RLS tenant-context transaction. They
// require a migrated PostgreSQL instance (orgs/folders tables + RLS
// policies + the app_user role) reachable at DATABASE_URL, and are gated
// behind the `integration` build tag so the db-less CI test-go job skips
// them. Run:
//
//	DATABASE_URL=postgres://... go test -tags=integration -race \
//	  ./services/api-go/internal/db/ -run TestWithTenant_Integration

func openTestPool(t *testing.T) *Pool {
	t.Helper()
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		t.Skip("DATABASE_URL not set; skipping integration test")
	}
	pool, err := NewPool(context.Background(), dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	return pool
}

func toPgUUID(id uuid.UUID) pgtype.UUID {
	return pgtype.UUID{Bytes: id, Valid: true}
}

// seedOrgWithFolder creates an org (orgs has no RLS) and one folder in it,
// using a superuser transaction so seeding itself doesn't depend on the code
// under test.
func seedOrgWithFolder(t *testing.T, pool *Pool) uuid.UUID {
	t.Helper()
	ctx := context.Background()
	orgID := uuid.New()

	q := repository.New(pool)
	if _, err := q.CreateOrg(ctx, repository.CreateOrgParams{
		ID:                toPgUUID(orgID),
		Name:              "t3-pool-" + orgID.String()[:8],
		UseKnowledgeGraph: pgtype.Bool{Bool: false, Valid: true},
		PermissionNumber:  pgtype.Int2{Int16: 0, Valid: true},
	}); err != nil {
		t.Fatalf("seed org: %v", err)
	}
	t.Cleanup(func() {
		// ON DELETE CASCADE removes the org's folders.
		_, _ = pool.Exec(context.Background(), "DELETE FROM orgs WHERE id = $1", orgID)
	})

	if _, err := q.CreateFolder(ctx, repository.CreateFolderParams{
		ID:      toPgUUID(uuid.New()),
		Name:    "seed-folder",
		DotPath: pgtype.Text{String: "seed-folder", Valid: true},
		OrgID:   toPgUUID(orgID),
	}); err != nil {
		t.Fatalf("seed folder: %v", err)
	}

	return orgID
}

// TestWithTenant_Integration verifies WithTenant runs on a single live
// transaction that (a) drops the connection role to app_user and (b) scopes
// every subsequent statement on that same tx to the given tenant via RLS —
// even a query with no explicit org_id predicate at all. This is the CRITICAL
// regression this fixes: the old implementation ran set_config on a bare
// autocommit connection, so the GUC was empty by the time the handler's real
// queries ran and cross-tenant rows leaked through.
func TestWithTenant_Integration(t *testing.T) {
	pool := openTestPool(t)
	defer pool.Close()

	org1 := seedOrgWithFolder(t, pool)
	org2 := seedOrgWithFolder(t, pool)
	ctx := context.Background()

	tc, err := pool.WithTenant(ctx, org1)
	if err != nil {
		t.Fatalf("WithTenant: %v", err)
	}
	defer tc.Release()

	// The connection must have dropped to app_user, not the pool's
	// (privileged/superuser) connection role — RLS is bypassed entirely for
	// superuser/BYPASSRLS roles regardless of the GUC.
	var currentUser string
	if err := tc.QueryRow(ctx, "SELECT current_user").Scan(&currentUser); err != nil {
		t.Fatalf("query current_user: %v", err)
	}
	if currentUser != "app_user" {
		t.Errorf("current_user = %q, want %q", currentUser, "app_user")
	}

	// The tenant GUC must be set and visible on this same transaction.
	var tenantSetting string
	if err := tc.QueryRow(ctx,
		"SELECT current_setting('app.current_tenant_id', true)",
	).Scan(&tenantSetting); err != nil {
		t.Fatalf("query tenant setting: %v", err)
	}
	if tenantSetting != org1.String() {
		t.Errorf("app.current_tenant_id = %q, want %q", tenantSetting, org1.String())
	}

	// A query with NO org_id predicate at all must still be scoped to org1 by
	// RLS alone — proving the tenant context survives past the single
	// set_config statement and applies to every later statement on this tx.
	var folderCount int
	if err := tc.QueryRow(ctx, "SELECT COUNT(*) FROM folders").Scan(&folderCount); err != nil {
		t.Fatalf("query folders count: %v", err)
	}
	if folderCount != 1 {
		t.Errorf("folders visible under org1 tenant context = %d, want 1 (org2's folder must not leak)", folderCount)
	}

	tc.Release()

	// A fresh WithTenant for org2 must see only org2's row — proving the
	// previous transaction's role/GUC were properly scoped to that
	// transaction and did not leak onto the shared pooled connection.
	tc2, err := pool.WithTenant(ctx, org2)
	if err != nil {
		t.Fatalf("WithTenant (org2): %v", err)
	}
	defer tc2.Release()

	var org2FolderCount int
	if err := tc2.QueryRow(ctx, "SELECT COUNT(*) FROM folders").Scan(&org2FolderCount); err != nil {
		t.Fatalf("query folders count (org2): %v", err)
	}
	if org2FolderCount != 1 {
		t.Errorf("folders visible under org2 tenant context = %d, want 1", org2FolderCount)
	}
}

// TestWithTenant_CommitPersistsWrites verifies that writes made through a
// TenantConn are actually persisted once Release() commits — guarding
// against a regression where Release() only rolls back (which would silently
// drop every create/update/delete made by handlers).
func TestWithTenant_CommitPersistsWrites(t *testing.T) {
	pool := openTestPool(t)
	defer pool.Close()

	orgID := seedOrgWithFolder(t, pool)
	ctx := context.Background()

	tc, err := pool.WithTenant(ctx, orgID)
	if err != nil {
		t.Fatalf("WithTenant: %v", err)
	}

	newFolderID := uuid.New()
	q := repository.New(tc)
	if _, err := q.CreateFolder(ctx, repository.CreateFolderParams{
		ID:      toPgUUID(newFolderID),
		Name:    "committed-folder",
		DotPath: pgtype.Text{String: "committed-folder", Valid: true},
		OrgID:   toPgUUID(orgID),
	}); err != nil {
		t.Fatalf("create folder on tenant tx: %v", err)
	}

	// Release() must commit — the write should be visible afterward.
	tc.Release()

	folder, err := repository.New(pool).GetFolder(ctx, toPgUUID(newFolderID))
	if err != nil {
		t.Fatalf("folder not persisted after Release(): %v", err)
	}
	if folder.Name != "committed-folder" {
		t.Errorf("persisted folder name = %q, want %q", folder.Name, "committed-folder")
	}
}
