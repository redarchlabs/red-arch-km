package db

import (
	"testing"
)

func TestDefaultConfig(t *testing.T) {
	cfg := DefaultConfig()

	if cfg.MaxConns != 25 {
		t.Errorf("MaxConns = %d, want 25", cfg.MaxConns)
	}
	if cfg.MinConns != 5 {
		t.Errorf("MinConns = %d, want 5", cfg.MinConns)
	}
}

// Integration tests require a running PostgreSQL instance.
// They are skipped by default and can be run with:
// DATABASE_URL=postgres://... go test -v -tags=integration
