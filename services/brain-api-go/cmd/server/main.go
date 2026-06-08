// Package main is the entry point for the Red Arch Brain API.
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
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/config"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/handlers"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/pipeline"
	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/stores"
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

	// Setup logging
	logging.SetDefault(cfg.LogLevel)
	slog.Info("starting Red Arch Brain API",
		"port", cfg.Port,
		"env", cfg.Env,
	)

	// Setup telemetry
	shutdownTelemetry, err := telemetry.Setup(ctx, telemetry.Config{
		ServiceName:    "red-arch-brain-api",
		ServiceVersion: "2.0.0",
		Environment:    cfg.Env,
		Enabled:        cfg.Env == "production",
	})
	if err != nil {
		return fmt.Errorf("setup telemetry: %w", err)
	}
	defer shutdownTelemetry(ctx)

	// Initialize OpenAI client
	if cfg.OpenAIAPIKey == "" {
		slog.Warn("OPENAI_API_KEY not set - LLM features will fail")
	}
	openaiClient := stores.NewOpenAIClient(cfg.OpenAIAPIKey, cfg.OpenAIEmbeddingModel, cfg.OpenAIChatModel)
	summarizer := stores.NewChunkSummarizer(openaiClient, 8, 10, 5, 300, 600)
	extractor := stores.NewTripletExtractor(openaiClient)

	// Initialize Qdrant client
	qdrantStore, err := stores.NewQdrantStore(
		cfg.QdrantURL,
		cfg.QdrantAPIKey,
		cfg.ChunkCollectionSuffix,
		cfg.DocCollectionSuffix,
		openaiClient.Dimension(),
	)
	if err != nil {
		return fmt.Errorf("create qdrant store: %w", err)
	}
	defer qdrantStore.Close()

	// Initialize Neo4j client (optional - may not be configured)
	var neo4jStore *stores.Neo4jStore
	if cfg.Neo4jPassword != "" {
		neo4jStore, err = stores.NewNeo4jStore(cfg.Neo4jURI, cfg.Neo4jUser, cfg.Neo4jPassword)
		if err != nil {
			slog.Warn("failed to create neo4j store - knowledge graph disabled", "error", err)
		} else {
			defer neo4jStore.Close(ctx)
		}
	} else {
		slog.Info("Neo4j password not set - knowledge graph disabled")
	}

	// Create pipeline
	ingestPipeline := pipeline.NewPipeline(qdrantStore, neo4jStore, openaiClient, summarizer, extractor)

	// Create handlers
	ingestHandlers := handlers.NewIngestHandlers(ingestPipeline)
	searchHandlers := handlers.NewSearchHandlers(ingestPipeline)

	// Setup router
	r := chi.NewRouter()

	// Global middleware
	r.Use(chimiddleware.RequestID)
	r.Use(chimiddleware.RealIP)
	r.Use(chimiddleware.Logger)
	r.Use(chimiddleware.Recoverer)
	r.Use(chimiddleware.Timeout(60 * time.Second))

	// CORS (internal service, more restrictive)
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{"http://localhost:*"},
		AllowedMethods:   []string{"GET", "POST", "PUT", "DELETE"},
		AllowedHeaders:   []string{"*"},
		MaxAge:           300,
	}))

	// Health endpoints
	r.Get("/healthz", handlers.Healthz())
	r.Get("/readyz", handlers.Readyz(handlers.ReadyzDeps{
		QdrantHealthy: func() bool { return qdrantStore.Healthy(ctx) },
		Neo4jHealthy: func() bool {
			if neo4jStore == nil {
				return true // Not configured, so "healthy" in the sense we don't depend on it
			}
			return neo4jStore.Healthy(ctx)
		},
	}))

	// API routes (require API key in production)
	r.Route("/", func(r chi.Router) {
		// Optional API key middleware
		if cfg.APIKey != "" {
			r.Use(apiKeyMiddleware(cfg.APIKey))
		}

		// Tenant management
		r.Post("/init-tenant", ingestHandlers.InitTenant())
		r.Post("/remove-tenant", ingestHandlers.RemoveTenant())

		// Document ingestion
		r.Post("/ingest-document", ingestHandlers.IngestDocument())
		r.Post("/remove-document", ingestHandlers.RemoveDocument())
		r.Post("/update-document-metadata", ingestHandlers.UpdateDocumentMetadata())

		// Read operations
		r.Get("/documents/{tenant}/{key}/chunks", ingestHandlers.GetDocumentChunks())

		// Search
		r.Post("/search", searchHandlers.Search())
		r.Post("/graph-search", searchHandlers.GraphSearch())
	})

	// Start server
	addr := fmt.Sprintf(":%d", cfg.Port)
	srv := &http.Server{
		Addr:         addr,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 60 * time.Second,
		IdleTimeout:  120 * time.Second,
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

// apiKeyMiddleware validates the X-API-Key header.
func apiKeyMiddleware(expectedKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Skip for health endpoints
			if r.URL.Path == "/healthz" || r.URL.Path == "/readyz" {
				next.ServeHTTP(w, r)
				return
			}

			apiKey := r.Header.Get("X-API-Key")
			if apiKey == "" {
				apiKey = r.Header.Get("Authorization")
				if len(apiKey) > 7 && apiKey[:7] == "Bearer " {
					apiKey = apiKey[7:]
				}
			}

			if apiKey != expectedKey {
				http.Error(w, `{"error": "invalid or missing API key"}`, http.StatusUnauthorized)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
