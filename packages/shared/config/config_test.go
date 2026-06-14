package config

import (
	"os"
	"testing"
)

func TestGetEnv(t *testing.T) {
	const testKey = "TEST_GET_ENV_KEY"
	defer os.Unsetenv(testKey)

	// Test default when not set
	if got := GetEnv(testKey, "default"); got != "default" {
		t.Errorf("GetEnv() = %q, want %q", got, "default")
	}

	// Test value when set
	os.Setenv(testKey, "value")
	if got := GetEnv(testKey, "default"); got != "value" {
		t.Errorf("GetEnv() = %q, want %q", got, "value")
	}
}

func TestGetEnvBool(t *testing.T) {
	const testKey = "TEST_GET_ENV_BOOL_KEY"
	defer os.Unsetenv(testKey)

	tests := []struct {
		envValue string
		defValue bool
		expected bool
	}{
		{"", true, true},   // not set, use default
		{"", false, false}, // not set, use default
		{"true", false, true},
		{"false", true, false},
		{"1", false, true},
		{"0", true, false},
		{"invalid", true, true}, // invalid, use default
	}

	for _, tt := range tests {
		if tt.envValue == "" {
			os.Unsetenv(testKey)
		} else {
			os.Setenv(testKey, tt.envValue)
		}
		if got := GetEnvBool(testKey, tt.defValue); got != tt.expected {
			t.Errorf("GetEnvBool(%q, %v) = %v, want %v", tt.envValue, tt.defValue, got, tt.expected)
		}
	}
}

func TestGetEnvInt(t *testing.T) {
	const testKey = "TEST_GET_ENV_INT_KEY"
	defer os.Unsetenv(testKey)

	tests := []struct {
		envValue string
		defValue int
		expected int
	}{
		{"", 42, 42},      // not set, use default
		{"100", 42, 100},  // valid int
		{"invalid", 42, 42}, // invalid, use default
	}

	for _, tt := range tests {
		if tt.envValue == "" {
			os.Unsetenv(testKey)
		} else {
			os.Setenv(testKey, tt.envValue)
		}
		if got := GetEnvInt(testKey, tt.defValue); got != tt.expected {
			t.Errorf("GetEnvInt(%q, %d) = %d, want %d", tt.envValue, tt.defValue, got, tt.expected)
		}
	}
}

func TestGetEnvStringSlice(t *testing.T) {
	const testKey = "TEST_GET_ENV_SLICE_KEY"
	defer os.Unsetenv(testKey)

	tests := []struct {
		envValue string
		defValue []string
		expected []string
	}{
		{"", []string{"a", "b"}, []string{"a", "b"}}, // not set
		{"x,y,z", nil, []string{"x", "y", "z"}},
		{"  a , b , c  ", nil, []string{"a", "b", "c"}}, // whitespace trimmed
	}

	for _, tt := range tests {
		if tt.envValue == "" {
			os.Unsetenv(testKey)
		} else {
			os.Setenv(testKey, tt.envValue)
		}
		got := GetEnvStringSlice(testKey, tt.defValue)
		if len(got) != len(tt.expected) {
			t.Errorf("GetEnvStringSlice(%q) len = %d, want %d", tt.envValue, len(got), len(tt.expected))
			continue
		}
		for i := range got {
			if got[i] != tt.expected[i] {
				t.Errorf("GetEnvStringSlice(%q)[%d] = %q, want %q", tt.envValue, i, got[i], tt.expected[i])
			}
		}
	}
}

func TestLoadBaseConfig(t *testing.T) {
	// Clear relevant env vars
	os.Unsetenv("ENV")
	os.Unsetenv("LOG_LEVEL")
	os.Unsetenv("DEBUG")

	cfg := LoadBaseConfig()
	if cfg.Env != "development" {
		t.Errorf("Env = %q, want %q", cfg.Env, "development")
	}
	if cfg.LogLevel != "INFO" {
		t.Errorf("LogLevel = %q, want %q", cfg.LogLevel, "INFO")
	}
	if cfg.Debug {
		t.Error("Debug = true, want false")
	}

	// Test with env vars set
	os.Setenv("ENV", "production")
	os.Setenv("LOG_LEVEL", "WARN")
	os.Setenv("DEBUG", "true")
	defer func() {
		os.Unsetenv("ENV")
		os.Unsetenv("LOG_LEVEL")
		os.Unsetenv("DEBUG")
	}()

	cfg = LoadBaseConfig()
	if cfg.Env != "production" {
		t.Errorf("Env = %q, want %q", cfg.Env, "production")
	}
	if cfg.LogLevel != "WARN" {
		t.Errorf("LogLevel = %q, want %q", cfg.LogLevel, "WARN")
	}
	if !cfg.Debug {
		t.Error("Debug = false, want true")
	}
}
