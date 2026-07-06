// Package db provides PostgreSQL connection pooling and RLS tenant context.
package db

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Pool wraps pgxpool.Pool with tenant context support.
type Pool struct {
	*pgxpool.Pool
}

// Config holds database pool configuration.
type Config struct {
	ConnString string
	MaxConns   int32
	MinConns   int32
}

// DefaultConfig returns default pool configuration.
func DefaultConfig() Config {
	return Config{
		MaxConns: 25,
		MinConns: 5,
	}
}

// NewPool creates a new connection pool.
func NewPool(ctx context.Context, connString string) (*Pool, error) {
	cfg := DefaultConfig()
	cfg.ConnString = connString
	return NewPoolWithConfig(ctx, cfg)
}

// NewPoolWithConfig creates a new connection pool with custom configuration.
func NewPoolWithConfig(ctx context.Context, cfg Config) (*Pool, error) {
	poolConfig, err := pgxpool.ParseConfig(cfg.ConnString)
	if err != nil {
		return nil, fmt.Errorf("parse connection string: %w", err)
	}

	poolConfig.MaxConns = cfg.MaxConns
	poolConfig.MinConns = cfg.MinConns

	pool, err := pgxpool.NewWithConfig(ctx, poolConfig)
	if err != nil {
		return nil, fmt.Errorf("create pool: %w", err)
	}

	// Verify connection
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping database: %w", err)
	}

	slog.Info("database pool created",
		"max_conns", cfg.MaxConns,
		"min_conns", cfg.MinConns,
	)

	return &Pool{Pool: pool}, nil
}

// Ping checks database connectivity.
func (p *Pool) Ping(ctx context.Context) error {
	return p.Pool.Ping(ctx)
}

// Close closes the connection pool.
func (p *Pool) Close() {
	p.Pool.Close()
	slog.Info("database pool closed")
}

// TenantConn is a single pooled connection with an open transaction that has
// RLS tenant context set. All statements MUST go through this transaction
// (via QueryRow/Query/Exec) — running a bare statement on the pooled
// connection outside this tx would not see the SET LOCAL role or the
// transaction-scoped set_config() below, and RLS would silently stop
// applying (see WithTenant).
type TenantConn struct {
	conn  *pgxpool.Conn
	tx    pgx.Tx
	ctx   context.Context
	orgID uuid.UUID
}

// Conn returns the underlying connection.
func (tc *TenantConn) Conn() *pgxpool.Conn {
	return tc.conn
}

// OrgID returns the tenant org ID.
func (tc *TenantConn) OrgID() uuid.UUID {
	return tc.orgID
}

// Release commits the tenant transaction and returns the connection to the
// pool. If the transaction was aborted by an earlier failed statement, the
// commit fails and the transaction is rolled back instead — so partial work
// from a failed request is never persisted. Safe to call more than once.
func (tc *TenantConn) Release() {
	if tc.tx != nil {
		if err := tc.tx.Commit(tc.ctx); err != nil && !errors.Is(err, pgx.ErrTxClosed) {
			slog.Warn("commit tenant transaction failed, rolling back", "error", err)
			if rbErr := tc.tx.Rollback(tc.ctx); rbErr != nil && !errors.Is(rbErr, pgx.ErrTxClosed) {
				slog.Warn("rollback tenant transaction failed", "error", rbErr)
			}
		}
		tc.tx = nil
	}
	if tc.conn != nil {
		tc.conn.Release()
		tc.conn = nil
	}
}

// QueryRow executes a query returning a single row, within the tenant transaction.
func (tc *TenantConn) QueryRow(ctx context.Context, sql string, args ...any) pgx.Row {
	return tc.tx.QueryRow(ctx, sql, args...)
}

// Query executes a query returning multiple rows, within the tenant transaction.
func (tc *TenantConn) Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error) {
	return tc.tx.Query(ctx, sql, args...)
}

// Exec executes a query without returning rows, within the tenant transaction.
func (tc *TenantConn) Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error) {
	return tc.tx.Exec(ctx, sql, args...)
}

// WithTenant acquires a connection, opens a single transaction on it, and
// sets RLS tenant context for the lifetime of that transaction.
//
// This mirrors the Python get_tenant_db dependency: both `SET LOCAL ROLE
// app_user` and `set_config('app.current_tenant_id', ..., true)` are
// transaction-scoped (SET LOCAL / is_local=true), so they MUST run inside an
// explicit transaction and every subsequent statement for this request MUST
// run on that same transaction (via the returned TenantConn's
// QueryRow/Query/Exec). Running set_config on a bare autocommit connection —
// as this used to do — scopes it to a single implicit transaction that ends
// as soon as the statement completes, leaving the tenant GUC empty (and RLS
// unenforced) for every query that follows.
//
// Dropping to app_user also matters: RLS is bypassed entirely for
// superuser/BYPASSRLS roles even under FORCE ROW LEVEL SECURITY, so without
// it the pool's (typically privileged) connection role would ignore tenant
// policies altogether regardless of the GUC.
//
// The caller MUST call Release() on the returned TenantConn when done; it
// commits the transaction (or rolls back if the transaction was aborted by a
// failed statement) and returns the connection to the pool.
func (p *Pool) WithTenant(ctx context.Context, orgID uuid.UUID) (*TenantConn, error) {
	conn, err := p.Acquire(ctx)
	if err != nil {
		return nil, fmt.Errorf("acquire connection: %w", err)
	}

	tx, err := conn.Begin(ctx)
	if err != nil {
		conn.Release()
		return nil, fmt.Errorf("begin transaction: %w", err)
	}

	// SET LOCAL is transaction-scoped and auto-resets on commit/rollback,
	// keeping the pooled connection clean for its next borrower.
	if _, err := tx.Exec(ctx, "SET LOCAL ROLE app_user"); err != nil {
		_ = tx.Rollback(ctx)
		conn.Release()
		return nil, fmt.Errorf("set role app_user: %w", err)
	}

	// set_config(name, value, is_local=true) scopes the setting to the
	// current transaction, so it applies to every statement run on tx until
	// Release() commits or rolls it back.
	if _, err := tx.Exec(ctx,
		"SELECT set_config('app.current_tenant_id', $1, true)",
		orgID.String(),
	); err != nil {
		_ = tx.Rollback(ctx)
		conn.Release()
		return nil, fmt.Errorf("set tenant context: %w", err)
	}

	return &TenantConn{conn: conn, tx: tx, ctx: ctx, orgID: orgID}, nil
}

// Health checks database health with a timeout.
func (p *Pool) Health(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	return p.Ping(ctx)
}
