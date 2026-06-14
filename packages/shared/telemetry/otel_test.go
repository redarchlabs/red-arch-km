package telemetry

import (
	"context"
	"testing"
)

func TestSetupDisabled(t *testing.T) {
	cfg := Config{
		ServiceName: "test-service",
		Enabled:     false,
	}

	shutdown, err := Setup(context.Background(), cfg)
	if err != nil {
		t.Fatalf("Setup() error = %v", err)
	}

	// Shutdown should be a no-op
	if err := shutdown(context.Background()); err != nil {
		t.Errorf("shutdown() error = %v", err)
	}
}

func TestTracer(t *testing.T) {
	tracer := Tracer("test-tracer")
	if tracer == nil {
		t.Error("Tracer() returned nil")
	}
}

func TestSpanFromContext(t *testing.T) {
	ctx := context.Background()
	span := SpanFromContext(ctx)
	if span == nil {
		t.Error("SpanFromContext() returned nil")
	}
	// Should return a no-op span for empty context
	if span.SpanContext().IsValid() {
		t.Error("expected invalid span context for background context")
	}
}
