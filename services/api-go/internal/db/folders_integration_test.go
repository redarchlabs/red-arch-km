//go:build integration

package db

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// Same requirements as pool_integration_test.go: a migrated PostgreSQL at
// DATABASE_URL, run with -tags=integration.

// TestWithTenant_RollbackDiscardsWrites: a handler that hits an error after a
// first write calls Rollback, and Release must not then commit that write.
func TestWithTenant_RollbackDiscardsWrites(t *testing.T) {
	pool := openTestPool(t)
	defer pool.Close()

	orgID := seedOrgWithFolder(t, pool)
	ctx := context.Background()

	tc, err := pool.WithTenant(ctx, orgID)
	if err != nil {
		t.Fatalf("WithTenant: %v", err)
	}
	folderID := uuid.New()
	if _, err := repository.New(tc).CreateFolder(ctx, repository.CreateFolderParams{
		ID:                         toPgUUID(folderID),
		Name:                       "rolled-back",
		DotPath:                    pgtype.Text{String: "rolled-back", Valid: true},
		ViewPermissionMasks:        []int64{},
		ContributorPermissionMasks: []int64{},
		OrgID:                      toPgUUID(orgID),
	}); err != nil {
		t.Fatalf("create folder on tenant tx: %v", err)
	}

	tc.Rollback()
	tc.Release()

	if _, err := repository.New(pool).GetFolder(ctx, toPgUUID(folderID)); !errors.Is(err, pgx.ErrNoRows) {
		t.Fatalf("GetFolder after Rollback: err = %v, want pgx.ErrNoRows", err)
	}
}

func createFolder(t *testing.T, q *repository.Queries, orgID uuid.UUID, parent pgtype.UUID, name, path string) pgtype.UUID {
	t.Helper()
	f, err := q.CreateFolder(context.Background(), repository.CreateFolderParams{
		ID:                         toPgUUID(uuid.New()),
		Name:                       name,
		DotPath:                    pgtype.Text{String: path, Valid: true},
		ViewPermissionMasks:        []int64{},
		ContributorPermissionMasks: []int64{},
		OrgID:                      toPgUUID(orgID),
		ParentID:                   parent,
	})
	if err != nil {
		t.Fatalf("create folder %q: %v", name, err)
	}
	return f.ID
}

// TestFolderSubtreeQueries_HeldToOrg runs the recursive folder queries on the
// pool's own (RLS-bypassing) connection, so only their org_id predicates stand
// between orgs: a folder in another org whose parent_id points into this tree
// must not be walked, counted or rewritten.
func TestFolderSubtreeQueries_HeldToOrg(t *testing.T) {
	pool := openTestPool(t)
	defer pool.Close()

	orgA := seedOrgWithFolder(t, pool)
	orgB := seedOrgWithFolder(t, pool)
	ctx := context.Background()
	q := repository.New(pool)

	root := createFolder(t, q, orgA, pgtype.UUID{}, "root", "root")
	child := createFolder(t, q, orgA, root, "child", "root.child")
	foreign := createFolder(t, q, orgB, root, "foreign", "foreign") // corrupt cross-org link

	descendants, err := q.GetFolderDescendants(ctx, repository.GetFolderDescendantsParams{ID: root, OrgID: toPgUUID(orgA)})
	if err != nil {
		t.Fatalf("GetFolderDescendants: %v", err)
	}
	ids := map[pgtype.UUID]bool{}
	for _, d := range descendants {
		ids[d.ID] = true
	}
	if len(ids) != 2 || !ids[root] || !ids[child] {
		t.Errorf("descendants = %v, want exactly root and child", ids)
	}

	count, err := q.CountFolderDescendants(ctx, repository.CountFolderDescendantsParams{ID: root, OrgID: toPgUUID(orgA)})
	if err != nil || count != 2 {
		t.Errorf("CountFolderDescendants = %d, %v; want 2", count, err)
	}

	wrongOrg, err := q.GetFolderDescendants(ctx, repository.GetFolderDescendantsParams{ID: root, OrgID: toPgUUID(orgB)})
	if err != nil || len(wrongOrg) != 0 {
		t.Errorf("GetFolderDescendants under another org = %d rows, %v; want 0", len(wrongOrg), err)
	}

	// Under the wrong org nothing is rewritten.
	if err := q.UpdateFolderDotPath(ctx, repository.UpdateFolderDotPathParams{
		NewPrefix: "hijack", FolderID: root, OrgID: toPgUUID(orgB),
	}); err != nil {
		t.Fatalf("UpdateFolderDotPath (wrong org): %v", err)
	}
	if err := q.UpdateFolderDotPath(ctx, repository.UpdateFolderDotPathParams{
		NewPrefix: "renamed", FolderID: root, OrgID: toPgUUID(orgA),
	}); err != nil {
		t.Fatalf("UpdateFolderDotPath: %v", err)
	}

	want := map[pgtype.UUID]string{root: "renamed", child: "renamed.child", foreign: "foreign"}
	for id, path := range want {
		f, err := q.GetFolder(ctx, id)
		if err != nil {
			t.Fatalf("GetFolder: %v", err)
		}
		if f.DotPath.String != path {
			t.Errorf("folder %q dot_path = %q, want %q", f.Name, f.DotPath.String, path)
		}
	}
}
