// Package config provides API service configuration.
package config

import (
	"github.com/redarchlabs/red-arch-km-2/packages/shared/config"
)

// Config holds all API service configuration.
type Config struct {
	config.BaseConfig

	// Server settings
	Port            int
	SecretKey       string
	CORSOrigins     []string
	RateLimitPerMin int

	// Database
	DatabaseURL string

	// Redis
	RedisURL string

	// Brain API
	BrainAPIURL string
	BrainAPIKey string

	// Internal API Key (for service-to-service calls)
	InternalAPIKey string

	// Clerk
	//   ClerkIssuer     = Clerk Frontend API URL (token `iss`).
	//   ClerkAllowedAZP = authorized-party allowlist (UI origins); required
	//                     for azp enforcement (G-AZP/R2). Comma-separated.
	//   ClerkSecretKey  = Clerk Backend API secret (sk_…); not needed for the
	//                     JWKS verify path, reserved for Backend-API provisioning.
	ClerkIssuer     string
	ClerkAllowedAZP []string
	ClerkSecretKey  string

	// E2E Test Mode (development only)
	E2ETestMode   bool
	E2ETestSecret string
}

// Load loads configuration from environment variables.
func Load() Config {
	base := config.LoadBaseConfig()

	return Config{
		BaseConfig: base,

		Port:            config.GetEnvInt("API_PORT", 8000),
		SecretKey:       config.GetEnv("API_SECRET_KEY", ""),
		CORSOrigins:     config.GetEnvStringSlice("API_CORS_ORIGINS", []string{"http://localhost:3000"}),
		RateLimitPerMin: config.GetEnvInt("API_RATE_LIMIT_PER_MINUTE", 60),

		DatabaseURL: config.GetEnv("DATABASE_URL", ""),
		RedisURL:    config.GetEnv("REDIS_URL", "redis://localhost:6379/0"),

		BrainAPIURL: config.GetEnv("API_BRAIN_API_URL", "http://localhost:8020"),
		BrainAPIKey: config.GetEnv("BRAIN_API_KEY", ""),

		InternalAPIKey: config.GetEnv("INTERNAL_API_KEY", ""),

		ClerkIssuer:     config.GetEnv("CLERK_JWT_ISSUER", ""),
		ClerkAllowedAZP: config.GetEnvStringSlice("CLERK_ALLOWED_AZP", nil),
		ClerkSecretKey:  config.GetEnv("CLERK_SECRET_KEY", ""),

		E2ETestMode:   config.GetEnvBool("API_E2E_TEST_MODE", false),
		E2ETestSecret: config.GetEnv("API_E2E_TEST_SECRET", ""),
	}
}

// Validate checks that required configuration is present.
func (c Config) Validate() error {
	// Whenever Clerk is enabled (any env), an azp allowlist is mandatory —
	// without it the verify path cannot enforce G-AZP and would be insecure.
	if c.ClerkIssuer != "" && len(c.ClerkAllowedAZP) == 0 {
		return ErrMissingClerkAllowedAZP
	}

	// In production, require certain fields.
	if c.Env == "production" {
		if c.DatabaseURL == "" {
			return ErrMissingDatabaseURL
		}
		// Clerk is the sole auth provider; its issuer must be configured.
		if c.ClerkIssuer == "" {
			return ErrMissingAuthProvider
		}
		if c.SecretKey == "" {
			return ErrMissingSecretKey
		}
	}
	return nil
}

// Error types for configuration validation.
type configError string

func (e configError) Error() string { return string(e) }

const (
	ErrMissingDatabaseURL     configError = "DATABASE_URL is required"
	ErrMissingAuthProvider    configError = "CLERK_JWT_ISSUER is required"
	ErrMissingClerkAllowedAZP configError = "CLERK_ALLOWED_AZP is required when CLERK_JWT_ISSUER is set"
	ErrMissingSecretKey       configError = "API_SECRET_KEY is required in production"
)
