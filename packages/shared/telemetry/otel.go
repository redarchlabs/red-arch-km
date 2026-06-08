// Package telemetry provides OpenTelemetry setup for tracing.
package telemetry

import (
	"context"
	"log/slog"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	"go.opentelemetry.io/otel/trace"
)

// Config holds telemetry configuration.
type Config struct {
	ServiceName    string
	ServiceVersion string
	Environment    string
	Enabled        bool
}

// Shutdown is a function that shuts down the tracer provider.
type Shutdown func(context.Context) error

// Setup initializes OpenTelemetry tracing.
// Returns a shutdown function that should be called when the application exits.
// If telemetry is disabled or setup fails, returns a no-op shutdown function.
func Setup(ctx context.Context, cfg Config) (Shutdown, error) {
	noopShutdown := func(context.Context) error { return nil }

	if !cfg.Enabled {
		slog.Info("telemetry disabled")
		return noopShutdown, nil
	}

	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		slog.Warn("failed to create OTLP exporter, tracing disabled", "error", err)
		return noopShutdown, nil // Don't fail startup for telemetry issues
	}

	res, err := resource.Merge(
		resource.Default(),
		resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceName(cfg.ServiceName),
			semconv.ServiceVersion(cfg.ServiceVersion),
			semconv.DeploymentEnvironment(cfg.Environment),
		),
	)
	if err != nil {
		slog.Warn("failed to create resource, tracing disabled", "error", err)
		return noopShutdown, nil
	}

	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
	)

	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	slog.Info("telemetry initialized", "service", cfg.ServiceName)

	return func(ctx context.Context) error {
		return tp.Shutdown(ctx)
	}, nil
}

// Tracer returns a named tracer from the global provider.
func Tracer(name string) trace.Tracer {
	return otel.Tracer(name)
}

// SpanFromContext extracts the current span from context.
func SpanFromContext(ctx context.Context) trace.Span {
	return trace.SpanFromContext(ctx)
}
