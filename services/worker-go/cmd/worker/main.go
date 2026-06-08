// Package main is the entry point for the Red Arch Background Worker.
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/logging"
	"github.com/redarchlabs/red-arch-km-2/packages/shared/telemetry"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/config"
)

func main() {
	if err := run(); err != nil {
		slog.Error("worker error", "error", err)
		os.Exit(1)
	}
}

func run() error {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Load configuration
	cfg := config.Load()

	// Setup logging
	logging.SetDefault(cfg.LogLevel)
	slog.Info("starting Red Arch Worker",
		"env", cfg.Env,
		"concurrency", cfg.Concurrency,
	)

	// Setup telemetry
	shutdownTelemetry, err := telemetry.Setup(ctx, telemetry.Config{
		ServiceName:    "red-arch-worker",
		ServiceVersion: "2.0.0",
		Environment:    cfg.Env,
		Enabled:        cfg.Env == "production",
	})
	if err != nil {
		slog.Warn("telemetry setup failed", "error", err)
	}
	defer shutdownTelemetry(ctx)

	// TODO: Initialize Redis client for task queue
	// TODO: Initialize API client
	// TODO: Initialize Brain API client
	// TODO: Register task handlers
	// TODO: Start worker pool

	slog.Info("worker initialized, waiting for tasks")

	// Wait for shutdown signal
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh

	slog.Info("shutdown signal received, stopping worker")

	// TODO: Graceful shutdown of worker pool

	return nil
}
