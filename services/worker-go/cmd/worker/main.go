// Package main is the entry point for the Red Arch Background Worker.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/logging"
	"github.com/redarchlabs/red-arch-km-2/packages/shared/telemetry"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/client"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/config"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/handlers"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/queue"
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
		"health_port", cfg.HealthPort,
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

	// Initialize clients
	brainClient := client.NewBrainClient(cfg.BrainAPIURL, cfg.BrainAPIKey)
	apiClient := client.NewAPIClient(cfg.APIURL, cfg.InternalAPIKey)

	// Initialize handlers
	ingestHandler := handlers.NewIngestHandler(brainClient, apiClient)
	removeHandler := handlers.NewRemoveHandler(brainClient)
	metadataHandler := handlers.NewMetadataHandler(brainClient)

	// Initialize queue inspector for health checks
	inspector, err := queue.NewInspector(cfg.RedisURL)
	if err != nil {
		return fmt.Errorf("create queue inspector: %w", err)
	}
	defer inspector.Close()

	// Initialize queue server
	queueServer, err := queue.NewServer(cfg.RedisURL, cfg, queue.ServerDeps{
		IngestHandler:   ingestHandler,
		RemoveHandler:   removeHandler,
		MetadataHandler: metadataHandler,
	})
	if err != nil {
		return fmt.Errorf("create queue server: %w", err)
	}

	// Start health server
	healthServer := startHealthServer(cfg.HealthPort, handlers.HealthDeps{
		QueueHealthy: func() bool { return inspector.Healthy(ctx) },
	})

	// Start queue server in goroutine
	errCh := make(chan error, 1)
	go func() {
		slog.Info("starting queue server")
		if err := queueServer.Start(); err != nil {
			errCh <- fmt.Errorf("queue server error: %w", err)
		}
	}()

	slog.Info("worker initialized, waiting for tasks")

	// Wait for shutdown signal or error
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	select {
	case sig := <-sigCh:
		slog.Info("shutdown signal received", "signal", sig)
	case err := <-errCh:
		slog.Error("worker error", "error", err)
	}

	slog.Info("shutting down worker")

	// Graceful shutdown
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer shutdownCancel()

	// Stop health server
	if err := healthServer.Shutdown(shutdownCtx); err != nil {
		slog.Warn("health server shutdown error", "error", err)
	}

	// Stop queue server
	queueServer.Shutdown()

	slog.Info("worker stopped")
	return nil
}

func startHealthServer(port int, deps handlers.HealthDeps) *http.Server {
	r := chi.NewRouter()
	r.Get("/healthz", handlers.Healthz())
	r.Get("/readyz", handlers.Readyz(deps))

	srv := &http.Server{
		Addr:         fmt.Sprintf(":%d", port),
		Handler:      r,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	go func() {
		slog.Info("health server listening", "addr", srv.Addr)
		if err := srv.ListenAndServe(); err != http.ErrServerClosed {
			slog.Error("health server error", "error", err)
		}
	}()

	return srv
}
