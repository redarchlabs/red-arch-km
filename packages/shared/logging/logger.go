// Package logging provides structured logging setup using slog.
package logging

import (
	"log/slog"
	"os"
	"strings"
)

// Setup creates a structured logger based on environment.
// In production (ENV=production), uses JSON format.
// Otherwise, uses text format for readability.
func Setup(level string) *slog.Logger {
	lvl := parseLevel(level)

	var handler slog.Handler
	if os.Getenv("ENV") == "production" {
		handler = slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: lvl})
	} else {
		handler = slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: lvl})
	}

	return slog.New(handler)
}

// SetDefault sets up and registers the default global logger.
func SetDefault(level string) *slog.Logger {
	logger := Setup(level)
	slog.SetDefault(logger)
	return logger
}

// parseLevel converts a string log level to slog.Level.
func parseLevel(level string) slog.Level {
	switch strings.ToUpper(level) {
	case "DEBUG":
		return slog.LevelDebug
	case "INFO":
		return slog.LevelInfo
	case "WARN", "WARNING":
		return slog.LevelWarn
	case "ERROR":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

// With returns a logger with the given attributes attached.
func With(logger *slog.Logger, attrs ...any) *slog.Logger {
	return logger.With(attrs...)
}

// WithRequestID returns a logger with request_id attribute.
func WithRequestID(logger *slog.Logger, requestID string) *slog.Logger {
	return logger.With("request_id", requestID)
}

// WithOrgID returns a logger with org_id attribute.
func WithOrgID(logger *slog.Logger, orgID string) *slog.Logger {
	return logger.With("org_id", orgID)
}
