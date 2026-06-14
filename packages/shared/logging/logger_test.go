package logging

import (
	"bytes"
	"log/slog"
	"os"
	"strings"
	"testing"
)

func TestParseLevel(t *testing.T) {
	tests := []struct {
		input    string
		expected slog.Level
	}{
		{"DEBUG", slog.LevelDebug},
		{"debug", slog.LevelDebug},
		{"INFO", slog.LevelInfo},
		{"info", slog.LevelInfo},
		{"WARN", slog.LevelWarn},
		{"warn", slog.LevelWarn},
		{"WARNING", slog.LevelWarn},
		{"ERROR", slog.LevelError},
		{"error", slog.LevelError},
		{"", slog.LevelInfo},        // default
		{"invalid", slog.LevelInfo}, // default
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := parseLevel(tt.input)
			if got != tt.expected {
				t.Errorf("parseLevel(%q) = %v, want %v", tt.input, got, tt.expected)
			}
		})
	}
}

func TestSetup(t *testing.T) {
	// Test development (text handler)
	os.Unsetenv("ENV")
	logger := Setup("INFO")
	if logger == nil {
		t.Fatal("Setup() returned nil")
	}

	// Test production (JSON handler)
	os.Setenv("ENV", "production")
	defer os.Unsetenv("ENV")
	logger = Setup("DEBUG")
	if logger == nil {
		t.Fatal("Setup() returned nil in production mode")
	}
}

func TestWithRequestID(t *testing.T) {
	var buf bytes.Buffer
	handler := slog.NewTextHandler(&buf, nil)
	logger := slog.New(handler)

	loggerWithReqID := WithRequestID(logger, "req-123")
	loggerWithReqID.Info("test message")

	output := buf.String()
	if !strings.Contains(output, "request_id=req-123") {
		t.Errorf("output %q does not contain request_id", output)
	}
}

func TestWithOrgID(t *testing.T) {
	var buf bytes.Buffer
	handler := slog.NewTextHandler(&buf, nil)
	logger := slog.New(handler)

	loggerWithOrgID := WithOrgID(logger, "org-456")
	loggerWithOrgID.Info("test message")

	output := buf.String()
	if !strings.Contains(output, "org_id=org-456") {
		t.Errorf("output %q does not contain org_id", output)
	}
}

func TestWith(t *testing.T) {
	var buf bytes.Buffer
	handler := slog.NewTextHandler(&buf, nil)
	logger := slog.New(handler)

	loggerWithAttrs := With(logger, "key1", "value1", "key2", 42)
	loggerWithAttrs.Info("test message")

	output := buf.String()
	if !strings.Contains(output, "key1=value1") {
		t.Errorf("output %q does not contain key1", output)
	}
	if !strings.Contains(output, "key2=42") {
		t.Errorf("output %q does not contain key2", output)
	}
}
