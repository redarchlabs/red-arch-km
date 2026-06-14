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
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/client"
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

	// Create Brain API client
	var brainClient *client.BrainAPIClient
	if cfg.BrainAPIURL != "" {
		brainClient = client.NewBrainAPIClient(client.BrainAPIConfig{
			BaseURL: cfg.BrainAPIURL,
			APIKey:  cfg.BrainAPIKey,
		})
	}

	// Create handlers
	orgHandler := handlers.NewOrgHandler(pool, brainClient)
	userHandler := handlers.NewUserHandler(pool)
	membershipHandler := handlers.NewMembershipHandler(pool)
	dimensionHandler := handlers.NewDimensionHandler(pool)

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

	// JWT middleware
	jwtMiddleware := middleware.NewJWTMiddleware(middleware.JWTConfig{
		KeycloakURL: cfg.KeycloakURL,
		Realm:       cfg.KeycloakRealm,
		ClientID:    cfg.KeycloakClientID,
	})

	// API routes (auth required)
	r.Route("/api", func(r chi.Router) {
		// Apply JWT auth to all /api routes
		r.Use(jwtMiddleware.Handler)

		// User routes
		r.Route("/users", func(r chi.Router) {
			r.Get("/me", userHandler.GetMe)
			r.Patch("/me", userHandler.UpdateMe)

			// Org-scoped user routes
			r.Group(func(r chi.Router) {
				r.Use(middleware.RequireOrg)
				r.Get("/", userHandler.ListUsersInOrg)
				r.Get("/{userID}", userHandler.GetUser)
			})
		})

		// Org routes (site-admin scoped for write, member for read)
		r.Route("/orgs", func(r chi.Router) {
			r.Get("/", orgHandler.ListOrgs)
			r.Post("/", orgHandler.CreateOrg)
			r.Get("/{orgID}", orgHandler.GetOrg)
			r.Patch("/{orgID}", orgHandler.UpdateOrg)
			r.Delete("/{orgID}", orgHandler.DeleteOrg)
		})

		// Membership routes (org-scoped, org-admin for write)
		r.Route("/memberships", func(r chi.Router) {
			r.Use(middleware.RequireOrg)
			r.Get("/", membershipHandler.ListMemberships)
			r.Post("/", membershipHandler.CreateMembership)
			r.Get("/by-user/{userID}", membershipHandler.GetMembershipByUser)
			r.Patch("/{membershipID}", membershipHandler.UpdateMembership)
			r.Delete("/{membershipID}", membershipHandler.DeleteMembership)
		})

		// Dimension routes (org-scoped)
		// Regions
		r.Route("/regions", func(r chi.Router) {
			r.Use(middleware.RequireOrg)
			r.Get("/", dimensionHandler.ListRegions)
			r.Post("/", dimensionHandler.CreateRegion)
			r.Get("/{dimensionID}", dimensionHandler.GetRegion)
			r.Patch("/{dimensionID}", dimensionHandler.UpdateRegion)
			r.Delete("/{dimensionID}", dimensionHandler.DeleteRegion)
		})

		// Departments
		r.Route("/departments", func(r chi.Router) {
			r.Use(middleware.RequireOrg)
			r.Get("/", dimensionHandler.ListDepartments)
			r.Post("/", dimensionHandler.CreateDepartment)
			r.Get("/{dimensionID}", dimensionHandler.GetDepartment)
			r.Patch("/{dimensionID}", dimensionHandler.UpdateDepartment)
			r.Delete("/{dimensionID}", dimensionHandler.DeleteDepartment)
		})

		// Roles
		r.Route("/roles", func(r chi.Router) {
			r.Use(middleware.RequireOrg)
			r.Get("/", dimensionHandler.ListRoles)
			r.Post("/", dimensionHandler.CreateRole)
			r.Get("/{dimensionID}", dimensionHandler.GetRole)
			r.Patch("/{dimensionID}", dimensionHandler.UpdateRole)
			r.Delete("/{dimensionID}", dimensionHandler.DeleteRole)
		})

		// Groups
		r.Route("/groups", func(r chi.Router) {
			r.Use(middleware.RequireOrg)
			r.Get("/", dimensionHandler.ListGroups)
			r.Post("/", dimensionHandler.CreateGroup)
			r.Get("/{dimensionID}", dimensionHandler.GetGroup)
			r.Patch("/{dimensionID}", dimensionHandler.UpdateGroup)
			r.Delete("/{dimensionID}", dimensionHandler.DeleteGroup)
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
