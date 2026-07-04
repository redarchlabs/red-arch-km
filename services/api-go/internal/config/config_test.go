package config

import (
	"os"
	"testing"
)

func TestLoad(t *testing.T) {
	// Clear env vars for clean test
	os.Unsetenv("DATABASE_URL")
	os.Unsetenv("API_PORT")

	cfg := Load()

	if cfg.Port != 8000 {
		t.Errorf("Port = %d, want 8000", cfg.Port)
	}

	// Test with env vars set
	os.Setenv("DATABASE_URL", "postgres://test:test@localhost/test")
	os.Setenv("API_PORT", "9000")
	defer func() {
		os.Unsetenv("DATABASE_URL")
		os.Unsetenv("API_PORT")
	}()

	cfg = Load()
	if cfg.Port != 9000 {
		t.Errorf("Port = %d, want 9000", cfg.Port)
	}
	if cfg.DatabaseURL != "postgres://test:test@localhost/test" {
		t.Errorf("DatabaseURL = %q", cfg.DatabaseURL)
	}
}

func TestValidate(t *testing.T) {
	// Development mode - no validation errors
	cfg := Load()
	if err := cfg.Validate(); err != nil {
		t.Errorf("Validate() error = %v in dev mode", err)
	}

	// Production mode - requires certain fields
	os.Setenv("ENV", "production")
	defer os.Unsetenv("ENV")

	cfg = Load()
	err := cfg.Validate()
	if err == nil {
		t.Error("Validate() should fail in production without required fields")
	}

	// Set required fields — Clerk is the sole auth provider; its issuer plus a
	// non-empty azp allowlist are required in production.
	os.Setenv("DATABASE_URL", "postgres://test:test@localhost/test")
	os.Setenv("CLERK_JWT_ISSUER", "https://clerk.example.com")
	os.Setenv("CLERK_ALLOWED_AZP", "http://localhost:3000")
	os.Setenv("API_SECRET_KEY", "secret")
	defer func() {
		os.Unsetenv("DATABASE_URL")
		os.Unsetenv("CLERK_JWT_ISSUER")
		os.Unsetenv("CLERK_ALLOWED_AZP")
		os.Unsetenv("API_SECRET_KEY")
	}()

	cfg = Load()
	if err := cfg.Validate(); err != nil {
		t.Errorf("Validate() error = %v with required fields", err)
	}
}
