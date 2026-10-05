package handlers

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

type fakePathRewriter struct {
	calls []repository.UpdateFolderDotPathParams
	err   error
}

func (f *fakePathRewriter) UpdateFolderDotPath(_ context.Context, arg repository.UpdateFolderDotPathParams) error {
	f.calls = append(f.calls, arg)
	return f.err
}

func TestRewriteSubtreeDotPaths(t *testing.T) {
	orgID := uuid.New()
	folder := repository.Folder{
		ID:      ToPgUUID(uuid.New()),
		DotPath: pgtype.Text{String: "a.b", Valid: true},
	}
	text := func(s string) pgtype.Text { return pgtype.Text{String: s, Valid: true} }

	t.Run("rewrites a changed path scoped to the org", func(t *testing.T) {
		q := &fakePathRewriter{}
		if err := rewriteSubtreeDotPaths(context.Background(), q, orgID, folder, text("x.b")); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(q.calls) != 1 {
			t.Fatalf("calls = %d, want 1", len(q.calls))
		}
		got := q.calls[0]
		if got.NewPrefix != "x.b" || got.FolderID != folder.ID || got.OrgID != ToPgUUID(orgID) {
			t.Errorf("params = %+v", got)
		}
	})

	t.Run("skips an unchanged or absent path", func(t *testing.T) {
		q := &fakePathRewriter{}
		for _, p := range []pgtype.Text{text("a.b"), {}} {
			if err := rewriteSubtreeDotPaths(context.Background(), q, orgID, folder, p); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
		}
		if len(q.calls) != 0 {
			t.Errorf("calls = %d, want 0", len(q.calls))
		}
	})

	t.Run("returns the failure so the handler can roll back", func(t *testing.T) {
		boom := errors.New("boom")
		q := &fakePathRewriter{err: boom}
		if err := rewriteSubtreeDotPaths(context.Background(), q, orgID, folder, text("x.b")); !errors.Is(err, boom) {
			t.Errorf("err = %v, want %v", err, boom)
		}
	})
}
