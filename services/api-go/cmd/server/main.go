// Package main is the entry point for the Red Arch Knowledge Manager API.
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
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"

	"github.com/redarchlabs/red-arch-km-2/packages/shared/logging"
	"github.com/redarchlabs/red-arch-km-2/packages/shared/telemetry"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/config"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/db"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/handlers"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/middleware"
)

func main() {
	if err := run(); err != nil {
		slog.Error("server error", "error", err)
		os.Exit(1)
	}
}

func run() error {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Load configuration
	cfg := config.Load()
	if err := cfg.Validate(); err != nil {
		return fmt.Errorf("config validation: %w", err)
	}

	// Setup logging
	logging.SetDefault(cfg.LogLevel)
	slog.Info("starting Red Arch KM API",
		"port", cfg.Port,
		"env", cfg.Env,
		"debug", cfg.Debug,
	)

	// Setup telemetry
	shutdownTelemetry, err := telemetry.Setup(ctx, telemetry.Config{
		ServiceName:    "red-arch-km-api",
		ServiceVersion: "2.0.0",
		Environment:    cfg.Env,
		Enabled:        cfg.Env == "production",
	})
	if err != nil {
		return fmt.Errorf("setup telemetry: %w", err)
	}
	defer shutdownTelemetry(ctx)

	// Create database pool
	var pool *db.Pool
	if cfg.DatabaseURL != "" {
		pool, err = db.NewPool(ctx, cfg.DatabaseURL)
		if err != nil {
			return fmt.Errorf("create db pool: %w", err)
		}
		defer pool.Close()
	} else {
		slog.Warn("DATABASE_URL not set, database features disabled")
	}

	// Setup router
	r := chi.NewRouter()

	// Global middleware
	r.Use(chimiddleware.RequestID)
	r.Use(chimiddleware.RealIP)
	r.Use(chimiddleware.Logger)
	r.Use(chimiddleware.Recoverer)
	r.Use(chimiddleware.Timeout(30 * time.Second))

	// CORS
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   cfg.CORSOrigins,
		AllowCredentials: true,
		AllowedMethods:   []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"*"},
		MaxAge:           300,
	}))

	// Health endpoints (no auth required)
	r.Get("/healthz", handlers.Healthz())
	if pool != nil {
		r.Get("/readyz", handlers.Readyz(pool))
	} else {
		r.Get("/readyz", handlers.Healthz()) // Fallback without DB
	}

	// JWT middleware (configured but not applied globally yet)
	jwtMiddleware := middleware.NewJWTMiddleware(middleware.JWTConfig{
		KeycloakURL: cfg.KeycloakURL,
		Realm:       cfg.KeycloakRealm,
		ClientID:    cfg.KeycloakClientID,
	})

	// API routes (auth required)
	r.Route("/api", func(r chi.Router) {
		// Apply JWT auth to all /api routes
		r.Use(jwtMiddleware.Handler)

		// Auth endpoints
		r.Route("/auth", func(r chi.Router) {
			r.Get("/me", handleMe)
		})

		// Org-scoped routes
		r.Route("/orgs", func(r chi.Router) {
			r.Get("/", handleListOrgs)
			// Nested routes requiring org context
			r.Route("/{orgID}", func(r chi.Router) {
				r.Use(middleware.RequireOrg)
				r.Get("/", handleGetOrg)
				// Add more org-scoped routes here
			})
		})
	})

	// Start server
	addr := fmt.Sprintf(":%d", cfg.Port)
	srv := &http.Server{
		Addr:         addr,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Graceful shutdown
	done := make(chan error)
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh

		slog.Info("shutdown signal received")

		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer shutdownCancel()

		done <- srv.Shutdown(shutdownCtx)
	}()

	slog.Info("server listening", "addr", addr)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		return fmt.Errorf("server error: %w", err)
	}

	return <-done
}

// Placeholder handlers - will be replaced with real implementations
func handleMe(w http.ResponseWriter, r *http.Request) {
	claims, ok := middleware.GetUserClaims(r.Context())
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"sub":"%s","email":"%s","username":"%s"}`, claims.Sub, claims.Email, claims.PreferredUsername)
}

func handleListOrgs(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte(`{"orgs":[]}`))
}

func handleGetOrg(w http.ResponseWriter, r *http.Request) {
	orgID := middleware.MustGetOrgID(r.Context())
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"id":"%s"}`, orgID)
}
