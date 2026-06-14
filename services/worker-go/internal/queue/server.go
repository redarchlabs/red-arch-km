package queue

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/hibiken/asynq"

	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/config"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/handlers"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/tasks"
)

// Server wraps asynq.Server for task processing.
type Server struct {
	server *asynq.Server
	mux    *asynq.ServeMux
}

// ServerDeps contains dependencies for creating a server.
type ServerDeps struct {
	IngestHandler   *handlers.IngestHandler
	RemoveHandler   *handlers.RemoveHandler
	MetadataHandler *handlers.MetadataHandler
}

// NewServer creates a new queue server.
func NewServer(redisURL string, cfg config.Config, deps ServerDeps) (*Server, error) {
	opt, err := asynq.ParseRedisURI(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}

	server := asynq.NewServer(opt, asynq.Config{
		Concurrency: cfg.Concurrency,
		Queues: map[string]int{
			"critical": 6,
			"default":  3,
			"low":      1,
		},
		RetryDelayFunc: func(n int, _ error, _ *asynq.Task) time.Duration {
			// Exponential backoff: 30s, 1m, 2m, 4m, ... capped at MaxDelay
			delay := cfg.RetryPolicy.InitialDelay * time.Duration(1<<uint(n))
			if delay > cfg.RetryPolicy.MaxDelay {
				delay = cfg.RetryPolicy.MaxDelay
			}
			return delay
		},
		Logger: &slogAdapter{},
	})

	mux := asynq.NewServeMux()

	// Register handlers
	mux.HandleFunc(tasks.TypeIngestDocument, deps.IngestHandler.ProcessTask)
	mux.HandleFunc(tasks.TypeRemoveDocument, deps.RemoveHandler.ProcessTask)
	mux.HandleFunc(tasks.TypeUpdateMetadata, deps.MetadataHandler.ProcessTask)

	return &Server{
		server: server,
		mux:    mux,
	}, nil
}

// Start starts the server and blocks until stopped.
func (s *Server) Start() error {
	return s.server.Run(s.mux)
}

// Shutdown gracefully shuts down the server.
func (s *Server) Shutdown() {
	s.server.Shutdown()
}

// slogAdapter adapts slog to asynq's Logger interface.
type slogAdapter struct{}

func (a *slogAdapter) Debug(args ...any) {
	slog.Debug(fmt.Sprint(args...))
}

func (a *slogAdapter) Info(args ...any) {
	slog.Info(fmt.Sprint(args...))
}

func (a *slogAdapter) Warn(args ...any) {
	slog.Warn(fmt.Sprint(args...))
}

func (a *slogAdapter) Error(args ...any) {
	slog.Error(fmt.Sprint(args...))
}

func (a *slogAdapter) Fatal(args ...any) {
	slog.Error(fmt.Sprint(args...))
}

// Inspector provides queue inspection capabilities.
type Inspector struct {
	inspector *asynq.Inspector
}

// NewInspector creates a new queue inspector.
func NewInspector(redisURL string) (*Inspector, error) {
	opt, err := asynq.ParseRedisURI(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}

	return &Inspector{
		inspector: asynq.NewInspector(opt),
	}, nil
}

// Close closes the inspector.
func (i *Inspector) Close() error {
	return i.inspector.Close()
}

// QueueStats returns statistics for the default queue.
func (i *Inspector) QueueStats(ctx context.Context) (*asynq.QueueInfo, error) {
	return i.inspector.GetQueueInfo("default")
}

// Healthy returns true if the queue is accessible.
func (i *Inspector) Healthy(ctx context.Context) bool {
	_, err := i.inspector.GetQueueInfo("default")
	return err == nil
}
