// Package config provides common configuration loading patterns for Red Arch services.
package config

import (
	"os"
	"strconv"
	"strings"
)

// GetEnv returns the value of an environment variable or the default if not set.
func GetEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

// GetEnvBool returns the boolean value of an environment variable or the default.
func GetEnvBool(key string, defaultValue bool) bool {
	value := os.Getenv(key)
	if value == "" {
		return defaultValue
	}
	b, err := strconv.ParseBool(value)
	if err != nil {
		return defaultValue
	}
	return b
}

// GetEnvInt returns the integer value of an environment variable or the default.
func GetEnvInt(key string, defaultValue int) int {
	value := os.Getenv(key)
	if value == "" {
		return defaultValue
	}
	i, err := strconv.Atoi(value)
	if err != nil {
		return defaultValue
	}
	return i
}

// GetEnvStringSlice returns a slice of strings from a comma-separated env variable.
func GetEnvStringSlice(key string, defaultValue []string) []string {
	value := os.Getenv(key)
	if value == "" {
		return defaultValue
	}
	parts := strings.Split(value, ",")
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		trimmed := strings.TrimSpace(p)
		if trimmed != "" {
			result = append(result, trimmed)
		}
	}
	return result
}

// BaseConfig contains common configuration shared across all services.
type BaseConfig struct {
	// Environment (development, staging, production)
	Env string
	// Log level (DEBUG, INFO, WARN, ERROR)
	LogLevel string
	// Whether debug mode is enabled
	Debug bool
}

// LoadBaseConfig loads common configuration from environment variables.
func LoadBaseConfig() BaseConfig {
	return BaseConfig{
		Env:      GetEnv("ENV", "development"),
		LogLevel: GetEnv("LOG_LEVEL", "INFO"),
		Debug:    GetEnvBool("DEBUG", false),
	}
}
