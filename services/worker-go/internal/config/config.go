// Package config provides Worker service configuration.
package config

import (
	"time"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/config"
)

// Config holds Worker service configuration.
type Config struct {
	config.BaseConfig

	// Redis for task queue
	RedisURL string

	// API endpoints
	APIURL      string
	BrainAPIURL string

	// API Keys
	InternalAPIKey string
	BrainAPIKey    string

	// Worker settings
	Concurrency int

	// HTTP server for health checks
	HealthPort int

	// Retry configuration
	RetryPolicy RetryPolicy
}

// RetryPolicy configures task retry behavior.
type RetryPolicy struct {
	// MaxRetries is the maximum number of retry attempts.
	MaxRetries int

	// InitialDelay is the delay before the first retry.
	InitialDelay time.Duration

	// MaxDelay is the maximum delay between retries.
	MaxDelay time.Duration

	// RetryableHTTPCodes are HTTP status codes that should trigger a retry.
	RetryableHTTPCodes []int
}

// DefaultRetryPolicy returns the default retry configuration.
func DefaultRetryPolicy() RetryPolicy {
	return RetryPolicy{
		MaxRetries:         3,
		InitialDelay:       30 * time.Second,
		MaxDelay:           5 * time.Minute,
		RetryableHTTPCodes: []int{429, 500, 502, 503, 504},
	}
}

// Load loads configuration from environment variables.
func Load() Config {
	base := config.LoadBaseConfig()

	return Config{
		BaseConfig: base,

		RedisURL: config.GetEnv("REDIS_URL", "redis://localhost:6379/0"),

		APIURL:      config.GetEnv("API_URL", "http://localhost:8000"),
		BrainAPIURL: config.GetEnv("BRAIN_API_URL", "http://localhost:8020"),

		InternalAPIKey: config.GetEnv("INTERNAL_API_KEY", ""),
		BrainAPIKey:    config.GetEnv("BRAIN_API_KEY", ""),

		Concurrency: config.GetEnvInt("WORKER_CONCURRENCY", 4),
		HealthPort:  config.GetEnvInt("WORKER_HEALTH_PORT", 8030),

		RetryPolicy: RetryPolicy{
			MaxRetries:         config.GetEnvInt("WORKER_MAX_RETRIES", 3),
			InitialDelay:       time.Duration(config.GetEnvInt("WORKER_RETRY_DELAY_SECONDS", 30)) * time.Second,
			MaxDelay:           time.Duration(config.GetEnvInt("WORKER_MAX_RETRY_DELAY_SECONDS", 300)) * time.Second,
			RetryableHTTPCodes: []int{429, 500, 502, 503, 504},
		},
	}
}
