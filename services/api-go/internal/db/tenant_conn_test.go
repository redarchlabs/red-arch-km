package db

import (
	"context"
	"testing"

	"github.com/jackc/pgx/v5"
)

// recordingTx is a pgx.Tx that records Commit/Rollback; every other method is
// the embedded nil interface and panics if reached.
type recordingTx struct {
	pgx.Tx
	commits, rollbacks int
}

func (r *recordingTx) Commit(context.Context) error   { r.commits++; return nil }
func (r *recordingTx) Rollback(context.Context) error { r.rollbacks++; return nil }

func TestTenantConnRollbackDiscardsTheTransaction(t *testing.T) {
	tx := &recordingTx{}
	tc := &TenantConn{tx: tx, ctx: context.Background()}

	tc.Rollback()
	tc.Release()  // must not commit what was rolled back
	tc.Rollback() // safe to repeat

	if tx.rollbacks != 1 {
		t.Errorf("rollbacks = %d, want 1", tx.rollbacks)
	}
	if tx.commits != 0 {
		t.Errorf("commits = %d, want 0 (Release after Rollback must not commit)", tx.commits)
	}
}

func TestTenantConnReleaseCommits(t *testing.T) {
	tx := &recordingTx{}
	tc := &TenantConn{tx: tx, ctx: context.Background()}

	tc.Release()

	if tx.commits != 1 || tx.rollbacks != 0 {
		t.Errorf("commits=%d rollbacks=%d, want 1 and 0", tx.commits, tx.rollbacks)
	}
}
