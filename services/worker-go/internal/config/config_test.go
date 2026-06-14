package config

import (
	"os"
	"testing"
	"time"
)

func TestLoad(t *testing.T) {
	// Clean env before test
	os.Unsetenv("REDIS_URL")
	os.Unsetenv("API_URL")
	os.Unsetenv("BRAIN_API_URL")
	os.Unsetenv("INTERNAL_API_KEY")
	os.Unsetenv("BRAIN_API_KEY")
	os.Unsetenv("WORKER_CONCURRENCY")
	os.Unsetenv("WORKER_HEALTH_PORT")
	os.Unsetenv("WORKER_MAX_RETRIES")
	os.Unsetenv("WORKER_RETRY_DELAY_SECONDS")
	os.Unsetenv("WORKER_MAX_RETRY_DELAY_SECONDS")

	t.Run("default values", func(t *testing.T) {
		cfg := Load()

		if cfg.RedisURL != "redis://localhost:6379/0" {
			t.Errorf("expected default Redis URL, got %s", cfg.RedisURL)
		}
		if cfg.APIURL != "http://localhost:8000" {
			t.Errorf("expected default API URL, got %s", cfg.APIURL)
		}
		if cfg.BrainAPIURL != "http://localhost:8020" {
			t.Errorf("expected default Brain API URL, got %s", cfg.BrainAPIURL)
		}
		if cfg.Concurrency != 4 {
			t.Errorf("expected concurrency 4, got %d", cfg.Concurrency)
		}
		if cfg.HealthPort != 8030 {
			t.Errorf("expected health port 8030, got %d", cfg.HealthPort)
		}
		if cfg.RetryPolicy.MaxRetries != 3 {
			t.Errorf("expected max retries 3, got %d", cfg.RetryPolicy.MaxRetries)
		}
		if cfg.RetryPolicy.InitialDelay != 30*time.Second {
			t.Errorf("expected initial delay 30s, got %v", cfg.RetryPolicy.InitialDelay)
		}
	})

	t.Run("custom values", func(t *testing.T) {
		os.Setenv("REDIS_URL", "redis://custom:6380/1")
		os.Setenv("API_URL", "http://api:9000")
		os.Setenv("BRAIN_API_URL", "http://brain:9020")
		os.Setenv("INTERNAL_API_KEY", "test-internal-key")
		os.Setenv("BRAIN_API_KEY", "test-brain-key")
		os.Setenv("WORKER_CONCURRENCY", "8")
		os.Setenv("WORKER_HEALTH_PORT", "8040")
		os.Setenv("WORKER_MAX_RETRIES", "5")
		os.Setenv("WORKER_RETRY_DELAY_SECONDS", "60")
		os.Setenv("WORKER_MAX_RETRY_DELAY_SECONDS", "600")

		cfg := Load()

		if cfg.RedisURL != "redis://custom:6380/1" {
			t.Errorf("expected custom Redis URL, got %s", cfg.RedisURL)
		}
		if cfg.APIURL != "http://api:9000" {
			t.Errorf("expected custom API URL, got %s", cfg.APIURL)
		}
		if cfg.BrainAPIURL != "http://brain:9020" {
			t.Errorf("expected custom Brain API URL, got %s", cfg.BrainAPIURL)
		}
		if cfg.InternalAPIKey != "test-internal-key" {
			t.Errorf("expected internal API key, got %s", cfg.InternalAPIKey)
		}
		if cfg.BrainAPIKey != "test-brain-key" {
			t.Errorf("expected brain API key, got %s", cfg.BrainAPIKey)
		}
		if cfg.Concurrency != 8 {
			t.Errorf("expected concurrency 8, got %d", cfg.Concurrency)
		}
		if cfg.HealthPort != 8040 {
			t.Errorf("expected health port 8040, got %d", cfg.HealthPort)
		}
		if cfg.RetryPolicy.MaxRetries != 5 {
			t.Errorf("expected max retries 5, got %d", cfg.RetryPolicy.MaxRetries)
		}
		if cfg.RetryPolicy.InitialDelay != 60*time.Second {
			t.Errorf("expected initial delay 60s, got %v", cfg.RetryPolicy.InitialDelay)
		}
		if cfg.RetryPolicy.MaxDelay != 600*time.Second {
			t.Errorf("expected max delay 600s, got %v", cfg.RetryPolicy.MaxDelay)
		}

		// Clean up
		os.Unsetenv("REDIS_URL")
		os.Unsetenv("API_URL")
		os.Unsetenv("BRAIN_API_URL")
		os.Unsetenv("INTERNAL_API_KEY")
		os.Unsetenv("BRAIN_API_KEY")
		os.Unsetenv("WORKER_CONCURRENCY")
		os.Unsetenv("WORKER_HEALTH_PORT")
		os.Unsetenv("WORKER_MAX_RETRIES")
		os.Unsetenv("WORKER_RETRY_DELAY_SECONDS")
		os.Unsetenv("WORKER_MAX_RETRY_DELAY_SECONDS")
	})
}

func TestDefaultRetryPolicy(t *testing.T) {
	policy := DefaultRetryPolicy()

	if policy.MaxRetries != 3 {
		t.Errorf("expected max retries 3, got %d", policy.MaxRetries)
	}
	if policy.InitialDelay != 30*time.Second {
		t.Errorf("expected initial delay 30s, got %v", policy.InitialDelay)
	}
	if policy.MaxDelay != 5*time.Minute {
		t.Errorf("expected max delay 5m, got %v", policy.MaxDelay)
	}
	if len(policy.RetryableHTTPCodes) != 5 {
		t.Errorf("expected 5 retryable codes, got %d", len(policy.RetryableHTTPCodes))
	}
}
