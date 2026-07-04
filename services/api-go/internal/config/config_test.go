package config

import (
	"errors"
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

// clearAuthEnv unsets every env var Validate() reads so each subtest starts from
// a known-empty baseline and only the vars it sets are in play. Without this,
// values leaking in from the host environment could mask a discriminating check.
func clearAuthEnv(t *testing.T) {
	t.Helper()
	for _, k := range []string{"ENV", "DATABASE_URL", "CLERK_JWT_ISSUER", "CLERK_ALLOWED_AZP", "API_SECRET_KEY"} {
		os.Unsetenv(k)
	}
}

func TestValidate(t *testing.T) {
	// Development mode - no validation errors when Clerk is not configured.
	clearAuthEnv(t)
	if err := Load().Validate(); err != nil {
		t.Errorf("Validate() error = %v in dev mode", err)
	}

	// The prod checks fire in order (DATABASE_URL → CLERK_JWT_ISSUER →
	// API_SECRET_KEY), and the azp guard fires globally whenever the issuer is
	// set. Each case below sets every field EXCEPT the one under test, then
	// asserts the SPECIFIC sentinel — a non-discriminating `err != nil` would
	// pass even if a different missing field tripped first, so it would not
	// actually prove the acceptance criterion.
	cases := []struct {
		name string
		env  map[string]string
		want error
	}{
		{
			// AC-6.2: production requires CLERK_JWT_ISSUER. Everything else the
			// prod path needs is present; only the issuer is missing, and azp is
			// set so the global guard cannot fire first.
			name: "prod_missing_clerk_issuer",
			env: map[string]string{
				"ENV":               "production",
				"DATABASE_URL":      "postgres://test:test@localhost/test",
				"CLERK_ALLOWED_AZP": "http://localhost:3000",
				"API_SECRET_KEY":    "secret",
			},
			want: ErrMissingAuthProvider,
		},
		{
			// G-AZP fail-open guard: issuer set but empty azp allowlist must be
			// rejected in ANY env (this is the branch with no other Go coverage —
			// delete config.go:79 and the rest of the suite stays green).
			name: "issuer_set_empty_azp",
			env: map[string]string{
				"CLERK_JWT_ISSUER": "https://clerk.example.com",
			},
			want: ErrMissingClerkAllowedAZP,
		},
		{
			// Prod with DATABASE_URL missing → its own sentinel, not the auth one.
			name: "prod_missing_database_url",
			env: map[string]string{
				"ENV":               "production",
				"CLERK_JWT_ISSUER":  "https://clerk.example.com",
				"CLERK_ALLOWED_AZP": "http://localhost:3000",
				"API_SECRET_KEY":    "secret",
			},
			want: ErrMissingDatabaseURL,
		},
		{
			// Prod with API_SECRET_KEY missing → its own sentinel.
			name: "prod_missing_secret_key",
			env: map[string]string{
				"ENV":               "production",
				"DATABASE_URL":      "postgres://test:test@localhost/test",
				"CLERK_JWT_ISSUER":  "https://clerk.example.com",
				"CLERK_ALLOWED_AZP": "http://localhost:3000",
			},
			want: ErrMissingSecretKey,
		},
		{
			// Fully configured production → no error.
			name: "prod_all_fields",
			env: map[string]string{
				"ENV":               "production",
				"DATABASE_URL":      "postgres://test:test@localhost/test",
				"CLERK_JWT_ISSUER":  "https://clerk.example.com",
				"CLERK_ALLOWED_AZP": "http://localhost:3000",
				"API_SECRET_KEY":    "secret",
			},
			want: nil,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			clearAuthEnv(t)
			for k, v := range tc.env {
				t.Setenv(k, v)
			}
			err := Load().Validate()
			if !errors.Is(err, tc.want) {
				t.Errorf("Validate() error = %v, want %v", err, tc.want)
			}
		})
	}
}
