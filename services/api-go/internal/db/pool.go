// Package db provides PostgreSQL connection pooling and RLS tenant context.
package db

import (
	"context"
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

// TenantConn is a connection with tenant context set.
type TenantConn struct {
	conn  *pgxpool.Conn
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

// Release returns the connection to the pool.
func (tc *TenantConn) Release() {
	tc.conn.Release()
}

// QueryRow executes a query returning a single row.
func (tc *TenantConn) QueryRow(ctx context.Context, sql string, args ...any) pgx.Row {
	return tc.conn.QueryRow(ctx, sql, args...)
}

// Query executes a query returning multiple rows.
func (tc *TenantConn) Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error) {
	return tc.conn.Query(ctx, sql, args...)
}

// Exec executes a query without returning rows.
func (tc *TenantConn) Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error) {
	return tc.conn.Exec(ctx, sql, args...)
}

// WithTenant acquires a connection with RLS tenant context set.
// The caller MUST call Release() on the returned TenantConn when done.
func (p *Pool) WithTenant(ctx context.Context, orgID uuid.UUID) (*TenantConn, error) {
	conn, err := p.Acquire(ctx)
	if err != nil {
		return nil, fmt.Errorf("acquire connection: %w", err)
	}

	// set_config(name, value, is_local=true) scopes the setting to the current transaction.
	// This ensures RLS policies use the correct tenant ID.
	_, err = conn.Exec(ctx,
		"SELECT set_config('app.current_tenant_id', $1, true)",
		orgID.String(),
	)
	if err != nil {
		conn.Release()
		return nil, fmt.Errorf("set tenant context: %w", err)
	}

	return &TenantConn{conn: conn, orgID: orgID}, nil
}

// Health checks database health with a timeout.
func (p *Pool) Health(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	return p.Ping(ctx)
}
