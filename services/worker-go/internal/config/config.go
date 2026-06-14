// Package config provides Worker service configuration.
package config

import (
	"github.com/redarchlabs/red-arch-km-2/packages/shared/config"
)

// Config holds Worker service configuration.
type Config struct {
	config.BaseConfig

	// Redis for task queue
	RedisURL string

	// API endpoints
	APIURL     string
	BrainAPIURL string

	// API Keys
	InternalAPIKey string
	BrainAPIKey    string

	// Worker settings
	Concurrency int
}

// Load loads configuration from environment variables.
func Load() Config {
	base := config.LoadBaseConfig()

	return Config{
		BaseConfig: base,

		RedisURL: config.GetEnv("REDIS_URL", "redis://localhost:6379/0"),

		APIURL:     config.GetEnv("API_URL", "http://localhost:8000"),
		BrainAPIURL: config.GetEnv("BRAIN_API_URL", "http://localhost:8020"),

		InternalAPIKey: config.GetEnv("INTERNAL_API_KEY", ""),
		BrainAPIKey:    config.GetEnv("BRAIN_API_KEY", ""),

		Concurrency: config.GetEnvInt("WORKER_CONCURRENCY", 4),
	}
}
